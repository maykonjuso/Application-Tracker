"""Backend de IA via Claude Code (`claude -p`) com um CLI falso que emite stream-json."""

from __future__ import annotations

import base64
import json
import stat
import sys
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select

from tracker.core.config import get_settings
from tracker.integrations.claude import client as ai
from tracker.modules.platform.models import AiCall

FAKE_CLI = r"""#!{python}
import json, sys, pathlib
args = sys.argv[1:]
prompt = sys.stdin.read()
log = pathlib.Path({log!r})
log.write_text(json.dumps({{"args": args, "prompt": prompt, "files": sorted(p.name for p in pathlib.Path.cwd().iterdir())}}))
def emit(e): print(json.dumps(e))
emit({{"type": "system", "subtype": "init"}})
if "WebSearch" in args[args.index("--tools") + 1]:
    emit({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": "WebSearch", "input": {{"query": "x"}}}}]}}}})
    links = '[{{"title":"Hist\\u00f3ria \\"oficial\\"","url":"https://exemplo.com/sobre"}},{{"title":"B","url":"https://b.com"}}]'
    emit({{"type": "user", "message": {{"content": [{{"type": "tool_result", "content": "Web search results\n\nLinks: " + links}}]}}}})
    emit({{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "name": "WebFetch", "input": {{"url": "https://c.com/x"}}}}]}}}})
if "ERRO" in prompt:
    emit({{"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "boom"}})
else:
    emit({{"type": "result", "subtype": "success", "is_error": False, "structured_output": {{"label": "ok", "n": 3}},
          "usage": {{"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100}}}})
"""


class Out(BaseModel):
    label: str
    n: int


@pytest.fixture
def fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "call.json"
    cli = tmp_path / "claude"
    cli.write_text(FAKE_CLI.format(python=sys.executable, log=str(log)))
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(get_settings(), "claude_code_cmd", str(cli))
    return log


async def test_structured_output_and_args(sessionmaker, fake_cli: Path) -> None:
    c = ai.ClaudeCodeClient(sessionmaker)
    res = await c.structured(ai.AIRequest(task="email.classify", system="SYS", user="<email>oi</email>", effort="low"), Out)
    assert res.output == Out(label="ok", n=3) and res.cost_usd == 0.0
    call = json.loads(fake_cli.read_text())
    args = call["args"]
    assert args[args.index("--tools") + 1] == ""  # sem ferramentas por padrão
    assert args[args.index("--effort") + 1] == "low"
    assert json.loads(args[args.index("--json-schema") + 1])["required"] == ["label", "n"]
    assert "--no-session-persistence" in args and call["prompt"] == "<email>oi</email>"
    assert "system.md" in call["files"]
    async with sessionmaker() as s:
        row = await s.scalar(select(AiCall).where(AiCall.task == "email.classify"))
    assert row is not None and row.model.endswith("(claude-code)") and row.cache_read_tokens == 100 and row.error is None


async def test_web_tools_collect_real_sources(sessionmaker, fake_cli: Path) -> None:
    res = await ai.ClaudeCodeClient(sessionmaker).structured(
        ai.AIRequest(task="company.profile", system="S", user="Empresa X", tools=[ai.WEB_SEARCH_TOOL, ai.WEB_FETCH_TOOL]), Out
    )
    args = json.loads(fake_cli.read_text())["args"]
    assert args[args.index("--tools") + 1] == "WebSearch,WebFetch"
    assert {"WebSearch", "WebFetch"} <= set(args[args.index("--allowedTools") + 1 :])
    assert [s["url"] for s in res.sources] == ["https://exemplo.com/sobre", "https://b.com", "https://c.com/x"]
    assert res.sources[0]["title"] == 'História "oficial"'


async def test_pdf_attachment_becomes_file_with_read_tool(sessionmaker, fake_cli: Path) -> None:
    pdf = base64.b64encode(b"%PDF-1.4 x").decode()
    user = [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf}},
        {"type": "text", "text": "Extraia o perfil."},
    ]
    await ai.ClaudeCodeClient(sessionmaker).structured(ai.AIRequest(task="profile.extract", system="S", user=user), Out)
    call = json.loads(fake_cli.read_text())
    assert "anexo_1.pdf" in call["files"] and "./anexo_1.pdf" in call["prompt"]
    assert call["args"][call["args"].index("--tools") + 1] == "Read"


async def test_error_is_recorded(sessionmaker, fake_cli: Path) -> None:
    with pytest.raises(ai.AIError, match="boom"):
        await ai.ClaudeCodeClient(sessionmaker).structured(ai.AIRequest(task="t.err", system="S", user="ERRO"), Out)
    async with sessionmaker() as s:
        row = await s.scalar(select(AiCall).where(AiCall.task == "t.err"))
    assert row is not None and "boom" in (row.error or "")


async def test_ollama_structured_output(sessionmaker) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "qwen2.5-coder:3b"
        assert body["format"]["required"] == ["label", "n"]
        assert body["messages"][-1]["content"] == "oi"
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5-coder:3b",
                "done_reason": "stop",
                "prompt_eval_count": 12,
                "eval_count": 8,
                "message": {"role": "assistant", "content": '{"label":"ok","n":3}'},
            },
        )

    client = ai.OllamaClient(sessionmaker, transport=httpx.MockTransport(respond))
    result = await client.structured(
        ai.AIRequest(task="email.classify", system="SYS", user="oi", model="qwen2.5-coder:3b"), Out
    )
    assert result.output == Out(label="ok", n=3) and result.cost_usd == 0.0
    async with sessionmaker() as s:
        row = await s.scalar(select(AiCall).where(AiCall.task == "email.classify"))
    assert row is not None and row.model == "qwen2.5-coder:3b" and row.input_tokens == 12 and row.error is None


async def test_ollama_extracts_text_from_pdf(sessionmaker) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    page[NameObject("/Resources")] = writer._add_object(resources)
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Curriculo Qwen) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    pdf = BytesIO()
    writer.write(pdf)
    encoded_pdf = base64.b64encode(pdf.getvalue()).decode()

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "Curriculo Qwen" in body["messages"][-1]["content"]
        return httpx.Response(200, json={"message": {"content": '{"label":"ok","n":3}'}, "done_reason": "stop"})

    client = ai.OllamaClient(sessionmaker, transport=httpx.MockTransport(respond))
    await client.structured(
        ai.AIRequest(
            task="profile.extract",
            system="SYS",
            user=[
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": encoded_pdf}},
                {"type": "text", "text": "Extraia o perfil."},
            ],
            model="qwen2.5-coder:3b",
        ),
        Out,
    )


def test_ollama_rejects_invalid_pdf_encoding() -> None:
    with pytest.raises(ai.AIError, match="não foi possível extrair texto"):
        ai.OllamaClient._text_content(
            [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "***"}}]
        )


async def test_ollama_rejects_unsupported_web_tools(sessionmaker) -> None:
    client = ai.OllamaClient(sessionmaker, transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    with pytest.raises(ai.AIError, match="ferramentas web"):
        await client.structured(
            ai.AIRequest(task="company.profile", system="S", user="Empresa", tools=[ai.WEB_SEARCH_TOOL]), Out
        )


def test_backend_selection(sessionmaker, fake_cli: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(ai, "_override", None)  # o conftest instala um FakeAIClient global
    monkeypatch.setattr(s, "ai_fake", False)
    monkeypatch.setattr(s, "anthropic_api_key", None)
    assert isinstance(ai.get_ai(sessionmaker), ai.ClaudeCodeClient)  # auto sem chave → Claude Code
    monkeypatch.setattr(s, "anthropic_api_key", "sk-ant-test")
    assert isinstance(ai.get_ai(sessionmaker), ai.ClaudeClient)  # auto com chave → API
    monkeypatch.setattr(s, "ai_backend", "claude_code")
    assert isinstance(ai.get_ai(sessionmaker), ai.ClaudeCodeClient)
    monkeypatch.setattr(s, "ai_backend", "ollama")
    assert isinstance(ai.get_ai(sessionmaker), ai.OllamaClient)
