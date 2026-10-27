"""Extensão real (Chromium) + API real (processo uvicorn) + página de candidatura local.

Fluxo: painel mostra a vaga conhecida → "Preencher" completa perfil/CV/anos → pergunta sensível fica
para o usuário → usuário responde e envia → extensão detecta "Thank you" → candidatura registrada e
resposta aprendida no banco.
"""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from playwright.async_api import async_playwright
from sqlalchemy import select

from tests import factories
from tracker.core import settings_store
from tracker.modules.applications.models import Application, ApplicationAnswer
from tracker.modules.jobs import service as job_service
from tracker.modules.jobs.normalize import RawJob
from tracker.modules.profile.models import AnswerBankEntry
from tracker.modules.profile.service import current_version
from tracker.modules.resumes.content import baseline
from tracker.modules.resumes.models import ResumeRender, ResumeVariant

ROOT = Path(__file__).resolve().parents[3]
EXT = ROOT / "apps" / "extension"
FIX = Path(__file__).resolve().parents[1] / "fixtures"
TOKEN = "test-extension-token"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def servers(tmp_path_factory):
    storage = tmp_path_factory.mktemp("ext-storage")
    api_port, web_port = free_port(), free_port()
    env = {
        **os.environ,
        "TRACKER_NO_DOTENV": "1",
        "APP_ENV": "test",
        "STORAGE_LOCAL_DIR": str(storage),
        "AI_FAKE": "true",
        "EMBEDDINGS_BACKEND": "hash",
        "CORS_ORIGINS": "[]",
    }
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "tracker.main:app", "--port", str(api_port), "--log-level", "warning"],
        env=env,
        cwd=ROOT / "backend",
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", web_port), partial(SimpleHTTPRequestHandler, directory=str(FIX)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    for _ in range(60):
        try:
            if httpx.get(f"http://127.0.0.1:{api_port}/api/v1/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    yield {"api": f"http://127.0.0.1:{api_port}", "web": f"http://127.0.0.1:{web_port}", "storage": storage}
    api.terminate()
    httpd.shutdown()


async def _seed(session, servers) -> str:
    from tracker.core.storage import LocalStorage

    m = await factories.seed_master(session)
    await settings_store.put(session, "extension", {"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()})
    page_url = f"{servers['web']}/apply_form.html"
    job, _ = await job_service.upsert_job(
        session,
        RawJob(
            source="browser",
            external_id="x1",
            title="Senior Data Engineer",
            company_name="Globex",
            url=page_url,
            description_text="Python Spark Kafka pipelines",
            apply_url=page_url,
        ),
    )
    v = ResumeVariant(
        kind="wildcard",
        slug="de-en",
        name="Data Engineer",
        language="en",
        status="approved",
        content=baseline(m, "data_engineer", "en").model_dump(),
        master_version=(await current_version(session)).version,
        validation={"passed": True},
    )
    session.add(v)
    await session.flush()
    key = LocalStorage(servers["storage"]).put("resumes/v/Ana_Silva_Data_Engineer_EN.pdf", b"%PDF-1.4 fake")
    session.add(
        ResumeRender(variant_id=v.id, storage_key=key, tex_source_key=key, filename="Ana_Silva_Data_Engineer_EN.pdf", sha256="x")
    )
    await session.commit()
    return str(job.id)


async def test_extension_end_to_end(servers, session, tmp_path) -> None:
    job_id = await _seed(session, servers)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(tmp_path / "profile"),
            headless=False,
            args=["--headless=new", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
        )
        try:
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
            await sw.evaluate(
                "cfg => chrome.storage.sync.set(cfg)",
                {"apiUrl": servers["api"], "token": TOKEN, "enabled": True, "ignoredDomains": []},
            )
            page = await ctx.new_page()
            await page.goto(f"{servers['web']}/apply_form.html")
            panel = page.locator("#tracker-assistant")
            await panel.get_by_text("Senior Data Engineer").wait_for(timeout=15000)  # cartão da vaga conhecida
            # preenche SOZINHO ao abrir (sem clicar em nada)
            await panel.get_by_text("preenchido(s) automaticamente").wait_for(timeout=15000)
            assert await page.input_value("#first") == "Ana"
            assert await page.input_value("#last") == "Teste Silva"
            assert await page.input_value("#email") == "ana@example.com"
            assert await page.input_value("#li") == "https://linkedin.com/in/ana-teste"
            assert int(await page.input_value("#years")) >= 5  # calculado pelo mestre
            assert await page.input_value("#eng") == "3"  # "Advanced" do perfil → opção "Avançado"
            assert await page.evaluate("document.getElementById('cv').files[0].name") == "Ana_Silva_Data_Engineer_EN.pdf"
            assert not await page.is_checked("#s-n")  # sensível: fica para o usuário
            # pergunta aberta: a IA já escreve sozinha depois do perfil (contorno azul, você revisa)
            await page.wait_for_function("document.getElementById('why').value.length > 10", timeout=20000)

            # etapa seguinte: campo novo é preenchido assim que aparece
            await page.click("#next")
            await page.wait_for_function("document.getElementById('gh').value.includes('ana-teste')", timeout=15000)

            # "Completar com IA" escreve a pergunta aberta
            await panel.get_by_text("Completar com IA").click()
            await page.wait_for_function("document.getElementById('why').value.length > 10", timeout=20000)

            # sugestão ao clicar num campo vazio, e "Usar" preenche de verdade
            await page.fill("#phone", "")
            await page.click("h1")
            await page.hover("#phone", position={"x": 4, "y": 4})  # parte visível: o painel sai da frente
            await page.wait_for_timeout(400)
            await page.click("#phone")
            await page.get_by_text("💡").wait_for(timeout=10000)
            # a sugestão é uma bolha pequena e escura, nunca um quadro esticado por cima do formulário
            box = await page.evaluate(
                "(() => { const b = [...document.querySelectorAll('div[role=dialog]')].find(d => d.textContent.includes('💡'));"
                " const r = b.getBoundingClientRect(); return {w: r.width, h: r.height, bg: getComputedStyle(b).backgroundColor}; })()"
            )
            assert box["h"] < 120 and box["w"] <= 400 and box["bg"] == "rgb(15, 23, 42)", box
            if os.environ.get("SHOT_DIR"):
                await page.screenshot(path=os.path.join(os.environ["SHOT_DIR"], "bubble.png"))
            await page.get_by_role("button", name="Usar").dispatch_event("mousedown")
            await page.wait_for_function("document.getElementById('phone').value.length > 5", timeout=5000)

            await panel.locator('button[title="Minimizar"]').click()  # tira o painel da frente
            await page.click("label[for=s-n]")  # o usuário responde a pergunta sensível
            await page.click("text=Submit application")  # e ele mesmo envia
            sent = await page.evaluate("window.sent")
            assert sent["spons"] == "No" and sent["cv"] == "Ana_Silva_Data_Engineer_EN.pdf" and sent["eng"] == "3"
            await page.locator("#tracker-assistant").get_by_text("acompanhamento").wait_for(timeout=15000)
        finally:
            await ctx.close()

    await session.close()
    app = await session.scalar(select(Application).where(Application.job_id == __import__("uuid").UUID(job_id)))
    assert app is not None and app.status == "submitted" and app.channel == "assisted"
    answers = (await session.scalars(select(ApplicationAnswer).where(ApplicationAnswer.application_id == app.id))).all()
    assert any("sponsorship" in a.question_text and a.answer["value"] == "No" and a.resolution == "user" for a in answers)
    assert any(a.resolution == "profile" and a.answer["value"] == "Ana" for a in answers)  # origem de cada resposta
    assert app.resume_render_id is not None  # o PDF exato que foi anexado (regra 6)
    # só o que o usuário digitou vira aprendizado; o que veio do perfil não duplica no banco
    assert await session.scalar(select(AnswerBankEntry).where(AnswerBankEntry.canonical_question == "First name")) is None
    learned = await session.scalar(select(AnswerBankEntry).where(AnswerBankEntry.canonical_question.like("%sponsorship%")))
    assert learned is not None and learned.answer["value"] == "No"  # da próxima vez, sem perguntar


async def test_extension_respects_context_and_switches(servers, session, tmp_path) -> None:
    """Página que não é de vaga: nada é preenchido nem anexado. Desligado ou site ignorado: nem o painel aparece."""
    await _seed(session, servers)
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(tmp_path / "profile2"),
            headless=False,
            args=["--headless=new", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
        )
        try:
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
            await sw.evaluate(
                "cfg => Promise.all([chrome.storage.sync.set(cfg.sync), chrome.storage.local.set({ token: cfg.token })])",
                {"sync": {"apiUrl": servers["api"], "enabled": True, "ignoredDomains": []}, "token": TOKEN},
            )
            page = await ctx.new_page()
            # a página não consegue ler o token pela extensão
            await page.goto(f"{servers['web']}/unrelated_form.html")
            await page.wait_for_timeout(3500)
            assert await page.input_value("#name") == ""
            assert await page.input_value("#mail") == ""
            assert await page.evaluate("document.getElementById('doc').files.length") == 0
            assert await page.locator("#tracker-assistant").count() == 0

            # desligado: nem na página de candidatura
            await sw.evaluate("() => chrome.storage.sync.set({ enabled: false })")
            await page.goto(f"{servers['web']}/apply_form.html")
            await page.wait_for_timeout(3500)
            assert await page.locator("#tracker-assistant").count() == 0
            assert await page.input_value("#first") == ""

            # ligar de novo na mesma aba: o assistente volta sem recarregar
            await sw.evaluate("() => chrome.storage.sync.set({ enabled: true })")
            await page.locator("#tracker-assistant").get_by_text("Senior Data Engineer").wait_for(timeout=15000)
            await page.wait_for_function("document.getElementById('first').value === 'Ana'", timeout=15000)

            # site ignorado: o painel some na hora
            host = servers["web"].removeprefix("http://")
            await sw.evaluate("h => chrome.storage.sync.set({ ignoredDomains: [h] })", host)
            await page.locator("#tracker-assistant").wait_for(state="detached", timeout=5000)

            # botões do painel não respondem a cliques simulados pela página
            await sw.evaluate("() => chrome.storage.sync.set({ ignoredDomains: [] })")
            await page.locator("#tracker-assistant").get_by_text("Já me candidatei").wait_for(timeout=15000)
            await page.evaluate(
                "[...document.getElementById('tracker-assistant').shadowRoot.querySelectorAll('button')]"
                ".find(b => b.textContent.includes('Já me candidatei')).click()"
            )
            await page.wait_for_timeout(1500)
        finally:
            await ctx.close()
    await session.close()
    apps = (await session.scalars(select(Application).where(Application.submitted_at.is_not(None)))).all()
    assert apps == []  # o clique forjado não registrou candidatura


async def test_panel_stays_usable_over_site_modal(servers, session, tmp_path) -> None:
    """Formulário dentro de <dialog> modal (top layer): o painel aparece por cima e responde a cliques."""
    await _seed(session, servers)
    page_url = f"{servers['web']}/modal_form.html"
    await job_service.upsert_job(
        session,
        RawJob(source="browser", external_id="m1", title="Senior Data Engineer", company_name="Globex", url=page_url,
               description_text="Python Spark", apply_url=page_url),
    )  # fmt: skip
    await session.commit()
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(tmp_path / "profile3"),
            headless=False,
            args=["--headless=new", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
        )
        try:
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
            await sw.evaluate(
                "cfg => Promise.all([chrome.storage.sync.set(cfg.sync), chrome.storage.local.set({ token: cfg.token })])",
                {"sync": {"apiUrl": servers["api"], "enabled": True, "ignoredDomains": []}, "token": TOKEN},
            )
            page = await ctx.new_page()
            await page.goto(page_url)
            panel = page.locator("#tracker-assistant")
            await panel.get_by_text("Gerar currículo para esta vaga").wait_for(timeout=15000)  # sempre na vaga conhecida
            await page.click("#open")
            await page.wait_for_function("document.getElementById('first').value === 'Ana'", timeout=15000)
            # o painel foi para dentro do modal e está na top layer
            assert await page.evaluate("!!document.querySelector('#dlg #tracker-assistant')")
            await panel.locator('button[title="Minimizar"]').click(timeout=5000)  # clique real funciona
            await panel.get_by_text("Assistente").first.wait_for(timeout=5000)
        finally:
            await ctx.close()


async def test_fields_labelled_by_nearby_text_not_placeholder(servers, session, tmp_path) -> None:
    """ATS sem <label>: rótulo em <div> acima e placeholder "Enter"/"Search". O assistente usa o texto de verdade."""
    await _seed(session, servers)
    page_url = f"{servers['web']}/generic_labels.html"
    await job_service.upsert_job(
        session,
        RawJob(source="browser", external_id="g1", title="Senior Full Stack Developer", company_name="Globex", url=page_url,
               description_text="Python Vue", apply_url=page_url),
    )  # fmt: skip
    await session.commit()
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(tmp_path / "profile4"),
            headless=False,
            args=["--headless=new", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
        )
        try:
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
            await sw.evaluate(
                "cfg => Promise.all([chrome.storage.sync.set(cfg.sync), chrome.storage.local.set({ token: cfg.token })])",
                {"sync": {"apiUrl": servers["api"], "enabled": True, "ignoredDomains": []}, "token": TOKEN},
            )
            page = await ctx.new_page()
            await page.goto(page_url)
            await page.wait_for_function("document.getElementById('a1').value === 'Ana'", timeout=15000)
            assert await page.input_value("#a2") == "Teste Silva"
            assert await page.input_value("#a3") == "ana@example.com"
            assert "linkedin.com/in/ana-teste" in await page.input_value("#a5")
            assert await page.evaluate("document.getElementById('a4').files.length") == 1
            await page.wait_for_function("window.picked === '5-7 years' || window.picked === '8+ years'", timeout=15000)
            assert await page.input_value("#a7") == ""  # quem indicou: não inventa
            await page.wait_for_function("document.getElementById('e4').checked", timeout=10000)  # inglês: Advanced
            assert not await page.is_checked("#e0")
            panel = page.locator("#tracker-assistant")
            assert await panel.get_by_text("Enter", exact=True).count() == 0  # nenhum campo chamado "Enter"
        finally:
            await ctx.close()


async def test_linkedin_easy_apply_attach_tailored_resume(servers, session, tmp_path) -> None:
    """Easy Apply: input de arquivo escondido atrás do botão "Carregar currículo" num passo que aparece depois.
    CV sob medida recém-gerado (rascunho) + um coringa já registrado na candidatura: "Aprovar e anexar"
    aprova e anexa o PDF da vaga (não o coringa)."""
    from tracker.core.storage import LocalStorage

    m = await factories.seed_master(session)
    await settings_store.put(session, "extension", {"token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest()})
    page_url = f"{servers['web']}/linkedin_easy_apply.html"
    job, _ = await job_service.upsert_job(
        session,
        RawJob(source="browser", external_id="li-ea", title="Engenheiro de Dados", company_name="Globex", url=page_url,
               description_text="Python Spark Kafka", apply_url=page_url, apply_kind="easy_apply"),
    )  # fmt: skip
    v = ResumeVariant(
        kind="tailored",
        job_id=job.id,
        slug="globex-de",
        name="Globex",
        language="pt",
        status="draft",
        content=baseline(m, "data_engineer", "pt").model_dump(),
        master_version=(await current_version(session)).version,
        validation={"passed": True},
    )
    session.add(v)
    await session.flush()
    key = LocalStorage(servers["storage"]).put("resumes/t/Ana_Silva_Globex.pdf", b"%PDF-1.4 tailored")
    session.add(ResumeRender(variant_id=v.id, storage_key=key, tex_source_key=key, filename="Ana_Silva_Globex.pdf", sha256="t"))
    w = ResumeVariant(kind="wildcard", slug="de-pt", name="Coringa", language="pt", status="approved",
                      content=baseline(m, "data_engineer", "pt").model_dump(),
                      master_version=(await current_version(session)).version, validation={"passed": True})  # fmt: skip
    session.add(w)
    await session.flush()
    wkey = LocalStorage(servers["storage"]).put("resumes/w/Ana_Coringa.pdf", b"%PDF-1.4 wildcard")
    wr = ResumeRender(variant_id=w.id, storage_key=wkey, tex_source_key=wkey, filename="Ana_Coringa.pdf", sha256="w")
    session.add(wr)
    await session.flush()
    session.add(Application(job_id=job.id, status="discovered", resume_render_id=wr.id))  # coringa escolhido antes
    await session.commit()
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(tmp_path / "profile-li"),
            headless=False,
            args=["--headless=new", f"--disable-extensions-except={EXT}", f"--load-extension={EXT}"],
        )
        try:
            sw = ctx.service_workers[0] if ctx.service_workers else await ctx.wait_for_event("serviceworker")
            await sw.evaluate(
                "cfg => Promise.all([chrome.storage.sync.set(cfg.sync), chrome.storage.local.set({ token: cfg.token })])",
                {"sync": {"apiUrl": servers["api"], "enabled": True, "ignoredDomains": [], "attachResume": False},
                 "token": TOKEN},
            )  # fmt: skip
            page = await ctx.new_page()
            await page.goto(page_url)
            panel = page.locator("#tracker-assistant")
            await panel.get_by_text("Aprovar e usar").wait_for(timeout=15000)  # sem campo de CV ainda
            await page.click("#easy")
            await page.click("#next")
            approve = panel.get_by_text("Aprovar e anexar")
            await approve.wait_for(timeout=15000)
            await approve.click()
            await page.wait_for_function(
                "document.getElementById('jobs-document-upload-file-input-upload-resume').files[0]?.name === 'Ana_Silva_Globex.pdf'",
                timeout=15000,
            )
            await panel.get_by_text("Anexar este CV no formulário").wait_for(timeout=5000)  # dá para reanexar depois
        finally:
            await ctx.close()
    await session.refresh(v)
    assert v.status == "approved"
