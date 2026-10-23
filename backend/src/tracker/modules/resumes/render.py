"""Render LaTeX: Jinja2 com delimitadores seguros → .tex → PDF (tectonic/pdflatex). docs/11 §3."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import jinja2
from pypdf import PdfReader

from tracker.core.config import REPO_ROOT
from tracker.modules.profile.schemas import MasterProfile
from tracker.modules.resumes.content import LABELS
from tracker.modules.resumes.latex import find_latex_compiler
from tracker.modules.resumes.schemas import ResumeContent

TEMPLATES_DIR = REPO_ROOT / "templates" / "resumes"

_TEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_TEX_RE = re.compile("|".join(re.escape(k) for k in _TEX_ESCAPES))


class RenderError(Exception):
    pass


def tex_escape(value: object) -> str:
    return _TEX_RE.sub(lambda m: _TEX_ESCAPES[m.group(0)], str(value or ""))


def md_bold(value: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", value)


def _env(template_dir: Path) -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template_dir)),
        block_start_string="((*",
        block_end_string="*))",
        variable_start_string="(((",
        variable_end_string=")))",
        comment_start_string="((#",
        comment_end_string="#))",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,  # noqa: S701 (saída LaTeX com escape próprio)
        undefined=jinja2.StrictUndefined,
    )
    env.filters["tex"] = tex_escape
    env.filters["md_bold"] = md_bold
    return env


def render_tex(content: ResumeContent, master: MasterProfile, template: str = "classic") -> str:
    tdir = TEMPLATES_DIR / template
    contact = master.contact.model_dump()
    ctx = {
        "contact": contact,
        "headline": content.headline,
        "summary": content.summary,
        "skill_rows": [r.model_dump() for r in content.skill_rows],
        "experience": [e.model_dump() for e in content.experience],
        "projects": [p.model_dump() for p in content.projects],
        "education": [e.model_dump() for e in content.education],
        "certifications": [c.model_dump() for c in content.certifications],
        "languages": [lang.model_dump() for lang in content.languages],
        "labels": LABELS.get(content.language, LABELS["en"]),
    }
    return _env(tdir).get_template("resume.tex.j2").render(**ctx)


def render_cover_letter_tex(
    text: str, master: MasterProfile, company: str, title: str, lang: str = "en", template: str = "classic"
) -> str:
    tdir = TEMPLATES_DIR / template
    return (
        _env(tdir)
        .get_template("cover_letter.tex.j2")
        .render(contact=master.contact.model_dump(), body=text, company=company, title=title, lang=lang)
    )


@dataclass
class CompiledPdf:
    pdf: bytes
    sha256: str
    page_count: int
    text: str


def compile_pdf(tex: str, template: str = "classic", timeout: int = 180) -> CompiledPdf:
    cmd = os.environ.get("LATEX_CMD", "tectonic")
    compiler = find_latex_compiler(cmd)
    if compiler is None:
        exe = shlex.split(cmd, posix=os.name != "nt")[0] if cmd else "tectonic"
        raise RenderError(f"compilador LaTeX '{exe}' não encontrado (instale tectonic no sistema ou no WSL2)")
    with tempfile.TemporaryDirectory(prefix="tracker-tex-") as tmp:
        tmpdir = Path(tmp)
        for f in (TEMPLATES_DIR / template).glob("*.sty"):
            shutil.copy(f, tmpdir / f.name)
        (tmpdir / "doc.tex").write_text(tex, encoding="utf-8")
        if compiler.tectonic:
            args = [*compiler.command, "-X", "compile"]
            cwd = tmpdir
            if compiler.wsl:
                path_result = subprocess.run(  # noqa: S603
                    [compiler.command[0], "--exec", "wslpath", "-u", str(tmpdir)],
                    capture_output=True,
                    check=False,
                    timeout=15,
                    text=True,
                    cwd=REPO_ROOT,
                )
                if path_result.returncode != 0 or not path_result.stdout.strip():
                    raise RenderError(f"não consegui converter o diretório temporário para WSL2: {path_result.stderr[-1000:]}")
                wsl_tmpdir = path_result.stdout.strip()
                args.extend(["--outdir", wsl_tmpdir, f"{wsl_tmpdir}/doc.tex"])
                cwd = REPO_ROOT
            else:
                args.append("doc.tex")
        else:
            args = [
                *compiler.command,
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "doc.tex",
            ]
            cwd = tmpdir
        proc = subprocess.run(  # noqa: S603
            args,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
            text=True,
            env={**os.environ, "openout_any": "p", "shell_escape": "f"},
        )
        pdf_path = tmpdir / "doc.pdf"
        if proc.returncode != 0 or not pdf_path.exists():
            tail = (proc.stderr or proc.stdout)[-1500:]
            raise RenderError(f"falha ao compilar LaTeX: {tail}")
        pdf = pdf_path.read_bytes()
    reader = PdfReader(io.BytesIO(pdf))
    # modo layout respeita o posicionamento (kerning do XeTeX); normaliza espaços por linha
    raw = "\n".join(p.extract_text(extraction_mode="layout") or "" for p in reader.pages)
    text = "\n".join(re.sub(r"[ \t]{2,}", " ", line).strip() for line in raw.splitlines() if line.strip())
    return CompiledPdf(pdf=pdf, sha256=hashlib.sha256(pdf).hexdigest(), page_count=len(reader.pages), text=text)


def pdf_filename(master: MasterProfile, variant_name: str, lang: str) -> str:
    parts = master.contact.full_name.split()
    who = f"{parts[0]}_{parts[-1]}" if len(parts) > 1 else parts[0]
    name = unicodedata.normalize("NFKD", variant_name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return f"{who}_{name}_{lang.upper()}.pdf"
