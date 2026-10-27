"""Tarefas de currículos: coringas, CV específico, render e carta de apresentação."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from tracker.core import events, settings_store
from tracker.core.humanize import humanize
from tracker.integrations.claude.client import AIClient, AIError, AIRequest, get_ai, load_prompt
from tracker.modules.jobs.models import Job
from tracker.modules.profile import service as profile_service
from tracker.modules.profile.schemas import MasterProfile
from tracker.modules.resumes import service
from tracker.modules.resumes.content import WILDCARD_PROFILES, from_draft
from tracker.modules.resumes.models import ResumeVariant
from tracker.modules.resumes.schemas import CoverLetter, ResumeContent, ResumeDraft
from tracker.modules.resumes.validator import validate, validate_free_text
from tracker.workers.celery_app import celery_app
from tracker.workers.runtime import SM, progress, tracked

LANG_NAMES = {"en": "inglês", "pt": "português do Brasil"}
PROMPT_VERSION = "v2"  # v2: impacto primeiro, texto do mestre no idioma de saída (bullets_pt/bullets_en)


def master_context(master: MasterProfile) -> str:
    return "<profile>\n" + master.model_dump_json(indent=1, exclude_none=True) + "\n</profile>"


async def generate_draft(
    ai: AIClient,
    master: MasterProfile,
    lang: str,
    instructions: str,
    task: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    effort: str = "high",
) -> tuple[ResumeContent, ResumeDraft]:
    """Gera rascunho, monta e valida; uma nova tentativa recebe as violações (docs/08 §5)."""
    prompt = load_prompt(task, PROMPT_VERSION)
    user = f"Idioma de saída: {LANG_NAMES[lang]}.\n\n{instructions}"
    for attempt in range(2):
        res = await ai.structured(
            AIRequest(
                task=task,
                system=prompt,
                context=master_context(master),
                user=user,
                effort=effort,  # type: ignore[arg-type]
                entity_type=entity_type,
                entity_id=entity_id,
                prompt_version=PROMPT_VERSION,
            ),
            ResumeDraft,
        )
        content = from_draft(master, res.output, lang)
        report = validate(content, master)
        if report.passed or attempt == 1:
            return content, res.output
        problems = "\n".join(f"- {v.text}" for v in report.violations)
        user = f"{user}\n\nA versão anterior violou fatos do perfil. Corrija SEM inventar:\n{problems}"
    raise AssertionError("inalcançável")


async def _wildcards(
    sm: SM, task_run_id: str | None, profiles: list[str] | None = None, languages: list[str] | None = None
) -> dict[str, Any]:
    profiles = profiles or ["data_engineer", "ai_ml_engineer", "software_engineer_backend"]
    languages = languages or ["en", "pt"]
    async with sm() as s:
        version, master = await profile_service.current_master(s)
    ai = get_ai(sm)
    created: list[str] = []
    total = len(profiles) * len(languages)
    i = 0
    for slug in profiles:
        spec = WILDCARD_PROFILES[slug]
        for lang in languages:
            i += 1
            await progress(sm, task_run_id, int(i / (total + 1) * 90), f"gerando {spec['name']} ({lang})")
            instructions = (
                f"Crie o currículo coringa '{spec['name']}'. Foco: {spec['focus']}.\n"
                f"Headline de referência: {master.headlines.get(str(spec['headline_key']), '')}"
            )
            content, draft = await generate_draft(ai, master, lang, instructions, "resume.wildcards")
            async with sm() as s:
                v = await service.create_variant(
                    s,
                    master_version=version,
                    master=master,
                    content=content,
                    kind="wildcard",
                    slug=f"{slug}-{lang}",
                    name=str(spec["name"]),
                    focus={"profile": slug, **spec},
                    changes=draft.changes,
                )
                try:
                    await service.render_variant(s, v, master)
                except service.ValidationFailed:
                    pass  # fica em rascunho com as violações visíveis
                await s.commit()
                created.append(str(v.id))
    return {"created": created}


celery_app.task(name="ai.resume_wildcards")(tracked("ai.resume_wildcards")(_wildcards))


def job_prompt_block(job: Job, company_name: str) -> str:
    req = job.requirements or {}
    return (
        f"<job>\nTítulo: {job.title}\nEmpresa: {company_name}\nIdioma: {job.language}\n"
        f"Obrigatórios: {', '.join(req.get('must', []))}\nDesejáveis: {', '.join(req.get('nice', []))}\n"
        f"Descrição:\n{job.description_text[:12000]}\n</job>"
    )


async def _tailor(sm: SM, task_run_id: str | None, job_id: str, base_variant_id: str | None = None) -> dict[str, Any]:
    from tracker.modules.companies.models import Company

    async with sm() as s:
        version, master = await profile_service.current_master(s)
        job = await s.get(Job, uuid.UUID(job_id))
        assert job is not None
        company = await s.get(Company, job.company_id)
        base = await s.get(ResumeVariant, uuid.UUID(base_variant_id)) if base_variant_id else None
        if base is None:
            sel = await service.select_for_job(s, job)
            base = await s.get(ResumeVariant, sel["best_variant_id"]) if sel["best_variant_id"] else None
        cfg = await settings_store.get(s, "resume")
        tailor_effort = str(cfg.get("tailor_effort") or "medium")
        if tailor_effort not in ("low", "medium", "high"):
            tailor_effort = "medium"
        job_block = job_prompt_block(job, company.name if company else "")
        lang = job.language if job.language in ("en", "pt") else "en"
        base_content = base.content if base else None
        base_id = base.id if base else None
    await progress(sm, task_run_id, 20, "adaptando o currículo à vaga")
    instructions = job_block
    if base_content:
        instructions += (
            "\n\nComece do coringa abaixo e adapte para a vaga:\n<base>\n"
            f"{ResumeContent.model_validate(base_content).model_dump_json()}\n</base>"
        )
    content, draft = await generate_draft(
        get_ai(sm), master, lang, instructions, "resume.tailor", "job", uuid.UUID(job_id), effort=tailor_effort
    )
    async with sm() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        assert job is not None
        v = await service.create_variant(
            s,
            master_version=version,
            master=master,
            content=content,
            kind="tailored",
            slug=f"tailored-{job_id[:8]}-{lang}",
            name=f"{job.title[:40]}",
            job_id=job.id,
            parent_variant_id=base_id,
            changes=draft.changes,
            status="draft" if cfg["tailored_requires_approval"] else "approved",
        )
        await progress(sm, task_run_id, 70, "compilando PDF")
        render_id = None
        try:
            r = await service.render_variant(s, v, master, list((job.requirements or {}).get("must", [])))
            render_id = str(r.id)
        except service.ValidationFailed:
            pass
        await s.commit()
    await events.apublish("resumes", "resume.tailored", {"variant_id": str(v.id), "job_id": job_id})
    return {"variant_id": str(v.id), "render_id": render_id, "passed": v.validation.get("passed")}


celery_app.task(name="ai.resume_tailor")(tracked("ai.resume_tailor")(_tailor))


async def _render(sm: SM, task_run_id: str | None, variant_id: str, keywords: list[str] | None = None) -> dict[str, Any]:
    async with sm() as s:
        _, master = await profile_service.current_master(s)
        v = await s.get(ResumeVariant, uuid.UUID(variant_id))
        assert v is not None
        try:
            r = await service.render_variant(s, v, master, keywords)
        except service.ValidationFailed as exc:
            await s.commit()
            raise RuntimeError(f"validação falhou: {exc}") from exc
        await s.commit()
        return {"render_id": str(r.id), "pages": r.page_count, "ats_ok": r.ats_report.get("ok")}


celery_app.task(name="docs.resume_render")(tracked("docs.resume_render")(_render))


async def generate_cover_letter(sm: SM, job_id: str, variant_id: str | None) -> CoverLetter:
    from tracker.modules.companies.models import Company

    async with sm() as s:
        _, master = await profile_service.current_master(s)
        job = await s.get(Job, uuid.UUID(job_id))
        assert job is not None
        company = await s.get(Company, job.company_id)
        variant = await s.get(ResumeVariant, uuid.UUID(variant_id)) if variant_id else None
        block = job_prompt_block(job, company.name if company else "")
        lang = job.language if job.language in ("en", "pt") else "en"
        vtext = variant.content if variant else None
    user = f"Idioma: {LANG_NAMES[lang]}.\n{block}"
    if vtext:
        user += f"\n<resume>{vtext}</resume>"
    res = None
    for _attempt in range(2):
        res = await get_ai(sm).structured(
            AIRequest(
                task="resume.cover_letter",
                system=load_prompt("resume.cover_letter"),
                context=master_context(master),
                user=user,
                effort="medium",
                entity_type="job",
                entity_id=uuid.UUID(job_id),
            ),
            CoverLetter,
        )
        res.output.text = humanize(res.output.text)
        report = validate_free_text(res.output.text, master, "carta")
        if report.passed:
            return res.output
        user += "\n\nCorrija sem inventar: " + "; ".join(v.text for v in report.violations)
    # reprovada duas vezes: nada inventado entra em formulário nenhum
    raise AIError("a carta gerada citou fatos que não estão no seu currículo; gere de novo ou escreva a sua")


async def _cover_letter(sm: SM, task_run_id: str | None, job_id: str, variant_id: str | None = None) -> dict[str, Any]:
    letter = await generate_cover_letter(sm, job_id, variant_id)
    async with sm() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        assert job is not None
        job.extra = {**(job.extra or {}), "cover_letter": {"text": letter.text, "variant_id": variant_id}}
        from tracker.modules.applications.models import Application

        app = await s.scalar(select(Application).where(Application.job_id == job.id))
        if app is not None:
            app.cover_letter = letter.text
        await s.commit()
    return {"words": len(letter.text.split())}


celery_app.task(name="ai.cover_letter")(tracked("ai.cover_letter")(_cover_letter))
