"""API do assistente do navegador (extensão Chrome/Edge). Auth: `Authorization: Extension <token>` ou sessão."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from tracker.api.deps import SessionDep
from tracker.core import audit, events, settings_store
from tracker.core.security import CSRF_HEADER, SESSION_COOKIE, CurrentUser, read_session
from tracker.core.storage import get_storage
from tracker.core.tasks import dispatch, enqueue
from tracker.integrations.claude.client import AIError, AIRequest, get_ai, load_prompt, sessionmaker_from
from tracker.modules.applications import service as app_service
from tracker.modules.applications.models import ApplicationAnswer
from tracker.modules.applications.resolver import FormField, resolve_core
from tracker.modules.assistant import service
from tracker.modules.jobs import service as job_service
from tracker.modules.jobs.models import Job
from tracker.modules.jobs.normalize import RawJob, detect_language, normalize_title
from tracker.modules.jobs.schemas import JobNormalized
from tracker.modules.notifications.service import notify
from tracker.modules.platform.models import TaskRun
from tracker.modules.profile import service as profile_service
from tracker.modules.profile.models import AnswerBankEntry
from tracker.modules.resumes import service as resume_service
from tracker.modules.resumes.models import ResumeRender, ResumeVariant

router = APIRouter(prefix="/assistant", tags=["assistant"])


async def assistant_auth(request: Request, session: SessionDep, authorization: Annotated[str | None, Header()] = None) -> str:
    if authorization and authorization.startswith("Extension "):
        cfg = await settings_store.get(session, "extension")
        digest = hashlib.sha256(authorization.removeprefix("Extension ").strip().encode()).hexdigest()
        if cfg.get("token_sha256") and hmac.compare_digest(digest, cfg["token_sha256"]):
            return "extension"
        raise HTTPException(401, "token da extensão inválido")
    # Cookie do dashboard: só do próprio dashboard (nunca de outra extensão) e com o header CSRF em escritas.
    origin = request.headers.get("origin", "")
    if read_session(request.cookies.get(SESSION_COOKIE)) and not origin.startswith(("chrome-extension://", "moz-extension://")):
        if request.method in ("GET", "HEAD", "OPTIONS") or request.headers.get(CSRF_HEADER) == "1":
            return "session"
    raise HTTPException(401, "token da extensão inválido")


Auth = Annotated[str, Depends(assistant_auth)]


@router.post("/token")
async def create_token(session: SessionDep, _user: CurrentUser) -> dict[str, str]:
    """Gera o token da extensão (mostrado uma única vez)."""
    token = secrets.token_urlsafe(32)
    cfg = await settings_store.get(session, "extension")
    await settings_store.put(session, "extension", {**cfg, "token_sha256": hashlib.sha256(token.encode()).hexdigest()})
    await audit.record(session, actor="user", action="extension.token")
    await session.commit()
    return {"token": token}


class PageIn(BaseModel):
    url: str
    title: str = ""
    text: str = Field("", max_length=60000)
    has_form: bool = False
    session_job_id: uuid.UUID | None = None  # vaga de onde você veio nesta aba (fluxo de candidatura em várias telas)


@router.post("/page")
async def page(body: PageIn, session: SessionDep, _a: Auth) -> dict[str, Any]:
    """Classifica a página e devolve o cartão da vaga se ela já for conhecida (sem IA)."""
    job = await service.find_job(session, body.url)
    if job is not None:
        card = await service.job_card(session, job)
        card["success_page"] = service.is_success_page(body.url, body.text)
        card["needs_details"] = service.is_thin(job) and service.looks_like_job(body.url, body.title, body.text)
        if body.has_form:
            card["tailor_task"] = await _auto_tailor(session, job, card)
        return card
    if service.is_flow_page(body.url):
        # login/etapas da candidatura: continua na vaga de origem, sem criar vaga nova nem gastar IA
        origin = await session.get(Job, body.session_job_id) if body.session_job_id else None
        if origin is not None and any(u and service.same_site(u, body.url) for u in (origin.canonical_url, origin.apply_url)):
            card = await service.job_card(session, origin)
            card.update(flow_page=True, success_page=service.is_success_page(body.url, body.text))
            return card
        return {"known": False, "looks_like_job": False, "auto_analyze": False, "flow_page": True,
                "success_page": service.is_success_page(body.url, body.text)}  # fmt: skip
    cfg = await settings_store.get(session, "extension")
    is_job = service.looks_like_job(body.url, body.title, body.text)
    return {
        "known": False,
        "looks_like_job": is_job,
        "auto_analyze": bool(
            is_job and cfg.get("auto_analyze_known_ats") and any(h in body.url for h in service.KNOWN_ATS_HOSTS)
        ),
        "success_page": service.is_success_page(body.url, body.text),
    }


async def _auto_tailor(session: SessionDep, job: Job, card: dict[str, Any]) -> str | None:
    """Formulário aberto de uma vaga boa (top/aposta) sem CV sob medida: já começa a gerar (ou devolve a geração em curso)."""
    running = await session.scalar(
        select(TaskRun)
        .where(TaskRun.kind == "ai.resume_tailor", TaskRun.entity_id == job.id, TaskRun.status.in_(("pending", "running")))
        .order_by(TaskRun.created_at.desc())
        .limit(1)
    )
    if running is not None:
        return str(running.id)
    from datetime import UTC, datetime, timedelta

    failed_recently = await session.scalar(
        select(TaskRun.id)
        .where(TaskRun.kind == "ai.resume_tailor", TaskRun.entity_id == job.id, TaskRun.status == "failed")
        .where(TaskRun.updated_at > datetime.now(UTC) - timedelta(hours=6))
        .limit(1)
    )
    if failed_recently is not None:
        return None  # não fica tentando a cada recarga; o botão "Gerar" continua disponível
    cfg = await settings_store.get(session, "resume")
    quadrant = (card.get("score") or {}).get("quadrant")
    if card.get("tailored") or not cfg.get("auto_tailor_on_apply", True) or quadrant not in ("top", "bet"):
        return None
    sel = await resume_service.select_for_job(session, job)
    if not sel["needs_tailored"]:
        return None  # o coringa já combina bem: não gasta IA
    tr = await enqueue(session, "ai.resume_tailor", entity_type="job", entity_id=job.id, job_id=str(job.id), base_variant_id=None)
    return str(tr.id)


async def _extract_page(session: SessionDep, body: PageIn) -> JobNormalized:
    try:
        res = await get_ai(sessionmaker_from(session)).structured(
            AIRequest(
                task="job.extract",
                system=load_prompt("job.extract"),
                effort="low",
                max_tokens=4000,
                cache_ttl_days=90,
                user=f"<job>\nURL: {service.normalize_url(body.url)}\nTítulo da página: {body.title}\n{body.text[:15000]}\n</job>",
            ),
            JobNormalized,
        )
    except AIError as exc:
        raise HTTPException(503, f"IA indisponível: {exc}") from exc
    return res.output


@router.post("/analyze")
async def analyze(body: PageIn, session: SessionDep, _a: Auth) -> dict[str, Any]:
    """Analisa a vaga da página (uma chamada de IA, cache de 90 dias) e avalia (Chance × Atratividade).

    Vaga já conhecida mas sem descrição é completada com o texto desta página e reavaliada."""
    job = await service.find_job(session, body.url)
    if job is None and service.is_flow_page(body.url):
        raise HTTPException(422, "esta tela é login ou etapa da candidatura, não uma vaga; abra a página da vaga")
    if job is not None and service.is_thin(job) and len(body.text) > 400:
        x = await _extract_page(session, body)
        job.description_text = body.text[:20000]
        job_service.apply_extraction(job, x)
        await session.commit()
    elif job is None:
        x = await _extract_page(session, body)
        if normalize_title(x.title) in service.NO_TITLE:
            raise HTTPException(422, "não encontrei uma vaga nesta página")
        existing = await service.find_job_same_site(session, body.url, x.title)
        if existing is not None:
            from tracker.modules.scoring.tasks import score_and_route

            await score_and_route(sessionmaker_from(session), str(existing.id))
            return await service.job_card(session, existing)
        source = "linkedin" if service.linkedin_job_id(body.url) else "browser"
        job, _ = await job_service.upsert_job(
            session,
            RawJob(
                source=source,
                external_id=service.external_id_for(body.url),
                title=x.title,
                company_name=x.company_name,
                url=service.normalize_url(body.url),
                location=x.location,
                description_text=body.text[:20000],
                apply_url=body.url,
                apply_kind=x.apply_kind if x.apply_kind != "unknown" else service.guess_apply_kind(body.url, body.text),
            ),
        )
        job_service.apply_extraction(job, x)
        await session.commit()
    from tracker.modules.scoring.tasks import score_and_route

    await score_and_route(sessionmaker_from(session), str(job.id))
    await session.refresh(job)
    return await service.job_card(session, job)


@router.get("/logo")
async def logo(company_id: uuid.UUID, session: SessionDep, _a: Auth) -> Response:
    """Logo da empresa para o painel da extensão (o mesmo cache do dashboard)."""
    from tracker.modules.companies.logos import company_logo
    from tracker.modules.companies.models import Company

    company = await session.get(Company, company_id)
    data = await company_logo(session, company) if company else None
    if data is None:
        raise HTTPException(404, "sem logo")
    return Response(data, media_type="image/png" if data[:4] == b"\x89PNG" else "image/x-icon")


class FieldsIn(BaseModel):
    url: str
    job_id: uuid.UUID | None = None
    fields: list[FormField]
    allow_ai: bool = True
    language: str | None = None


def _transient_job(language: str) -> Job:
    return Job(id=uuid.UUID(int=0), title="", language=language, requirements={}, regime=None, salary=None)


@router.post("/fields")
async def fields(body: FieldsIn, session: SessionDep, _a: Auth) -> dict[str, Any]:
    """Resolve os campos do formulário da página (perfil → banco → regras → IA em lote)."""
    out = await _resolve(body, session)
    await session.commit()  # uso do banco de respostas (use_count/aliases) só conta quando preenche
    return out


async def _resolve(body: FieldsIn, session: SessionDep) -> dict[str, Any]:
    job = await session.get(Job, body.job_id) if body.job_id else await service.find_job(session, body.url)
    cover = None
    if job is not None:
        app = await app_service.get_for_job(session, job.id)
        cover = app.cover_letter if app else (((job.extra or {}).get("cover_letter") or {}).get("text"))
    target = job or _transient_job(body.language or "pt")
    res, ai = await resolve_core(session, body.fields, target, cover, allow_ai=body.allow_ai)
    return {
        "values": {r.key: {"value": r.value, "source": r.resolution, "confidence": r.confidence} for r in res.values},
        "suggestions": {
            k: {"value": a.value, "confidence": a.confidence} for k, a in ai.items() if k not in {r.key for r in res.values}
        },
        # sensível de verdade (salário, visto, dados pessoais protegidos): nunca vai para a IA, quem responde é você
        "sensitive": [f.key for f in res.pending if f.key not in ai and profile_service.is_sensitive(f.label)],
        # sem resposta salva: a IA pode escrever quando você pedir ("Completar com IA")
        "open": [f.key for f in res.pending if f.key not in ai and not profile_service.is_sensitive(f.label)],
        "gaps": res.gaps,
        "job_id": str(job.id) if job else None,
    }


class SuggestIn(BaseModel):
    url: str
    job_id: uuid.UUID | None = None
    field: FormField
    allow_ai: bool = False


@router.post("/suggest")
async def suggest(body: SuggestIn, session: SessionDep, _a: Auth) -> dict[str, Any]:
    """Sugestão para um campo clicado. IA só se `allow_ai` (botão "✨ IA" na extensão)."""
    out = await _resolve(FieldsIn(url=body.url, job_id=body.job_id, fields=[body.field], allow_ai=body.allow_ai), session)
    await session.rollback()  # só olhar a sugestão não altera o banco de respostas
    key = body.field.key
    if key in out["values"]:
        return {"key": key, **out["values"][key], "sensitive": False}
    if key in out["suggestions"]:
        return {"key": key, **out["suggestions"][key], "source": "ai", "sensitive": False}
    return {"key": key, "value": None, "source": None, "sensitive": key in out["sensitive"]}


class LearnItem(BaseModel):
    question: str
    value: Any
    field_kind: str = "text"
    options: list[str] = Field(default_factory=list)
    language: str = "any"
    # quem deu a resposta: user (você digitou), profile/bank/rule (assistente), ai (IA). Só "user" vira aprendizado.
    source: str = Field(default="user", pattern="^(user|profile|bank|rule|ai)$")


class LearnIn(BaseModel):
    items: list[LearnItem]


@router.post("/learn")
async def learn(body: LearnIn, session: SessionDep, _a: Auth) -> dict[str, int]:
    """Respostas que você digitou viram banco de respostas (a mesma pergunta não gasta IA de novo)."""
    saved = 0
    rows = (await session.scalars(select(AnswerBankEntry))).all()
    by_q = {profile_service.normalize_question(r.canonical_question): r for r in rows}
    for it in body.items:
        if it.value in (None, "", []) or len(it.question) < 3 or it.field_kind == "file":
            continue
        nq = profile_service.normalize_question(it.question)
        entry = by_q.get(nq)
        if entry is None:
            entry = AnswerBankEntry(
                canonical_question=it.question.strip(),
                field_kind=it.field_kind,
                answer={"value": it.value},
                language=it.language,
                source="user",
                sensitive=profile_service.is_sensitive(it.question),
            )
            await profile_service.upsert_answer(session, entry)
            by_q[nq] = entry
        else:
            entry.answer = {**(entry.answer or {}), "value": it.value}
        saved += 1
    await session.commit()
    return {"saved": saved}


class SubmittedIn(BaseModel):
    url: str
    title: str = ""
    text: str = Field("", max_length=60000)
    job_id: uuid.UUID | None = None
    company: str | None = None
    job_title: str | None = None
    answers: list[LearnItem] = Field(default_factory=list)
    render_id: uuid.UUID | None = None  # PDF que a extensão anexou (snapshot do que foi enviado, regra 6)


@router.post("/submitted")
async def submitted(body: SubmittedIn, session: SessionDep, _a: Auth) -> dict[str, Any]:
    """Você enviou a candidatura no site: registra no acompanhamento (com o que foi respondido)."""
    new_job: uuid.UUID | None = None
    job = await session.get(Job, body.job_id) if body.job_id else await service.find_job(session, body.url)
    if job is None and body.job_title:
        job = await service.find_job_same_site(session, body.url, body.job_title)
    if job is None:
        company = body.company or body.title.split(" - ")[-1].split(" | ")[-1].strip() or "Empresa (a confirmar)"
        job, _ = await job_service.upsert_job(
            session,
            RawJob(
                source="browser",
                external_id=service.external_id_for(body.url),
                title=body.job_title or body.title.split(" | ")[0][:200] or "Vaga",
                company_name=company,
                url=service.normalize_url(body.url),
                description_text=body.text[:20000],
                apply_url=body.url,
                apply_kind=service.guess_apply_kind(body.url, body.text),
            ),
        )
        job.language = detect_language(body.text) if body.text else job.language
        await session.flush()
        new_job = job.id
    app = await service.ensure_application(session, job)
    already = app.submitted_at is not None
    if not already:
        if body.render_id and app.resume_render_id is None and await session.get(ResumeRender, body.render_id):
            app.resume_render_id = body.render_id
        await app_service.register_submission(session, app, actor="user", channel="assisted")
        for i, a in enumerate(body.answers):
            if a.value in (None, "", []):
                continue
            session.add(
                ApplicationAnswer(
                    application_id=app.id,
                    step_index=i,
                    question_text=a.question[:500],
                    field_kind=a.field_kind,
                    options=a.options,
                    answer={"value": a.value},
                    resolution=a.source,
                    confidence=1.0 if a.source == "user" else 0.9,
                    locked=True,
                )
            )
        await notify(
            session,
            "review_ready",
            {"title": job.title, "message": "Candidatura registrada pelo assistente do navegador", "application_id": str(app.id)},
            telegram_too=False,
        )
    await session.commit()
    if new_job is not None:
        dispatch("ai.process_job", job_id=str(new_job))
    mine = [a for a in body.answers if a.source == "user"]
    if mine and not already:
        await learn(LearnIn(items=mine), session, _a)
    await events.apublish("applications", "application.updated", {"application_id": str(app.id), "to": app.status})
    return {"application_id": str(app.id), "status": app.status, "already": already, "job_id": str(job.id)}


class TailorIn(BaseModel):
    job_id: uuid.UUID


@router.post("/tailor", status_code=202)
async def tailor(body: TailorIn, session: SessionDep, _a: Auth) -> dict[str, str]:
    """Gera o CV sob medida para a vaga (IA + validador de fatos). Acompanhe em GET /assistant/tasks/{id}."""
    job = await session.get(Job, body.job_id)
    if job is None:
        raise HTTPException(404, "vaga não encontrada: avalie a vaga primeiro")
    tr = await enqueue(session, "ai.resume_tailor", entity_type="job", entity_id=job.id, job_id=str(job.id), base_variant_id=None)
    return {"task_id": str(tr.id)}


@router.get("/tasks/{task_id}")
async def task_status(task_id: uuid.UUID, session: SessionDep, _a: Auth) -> dict[str, Any]:
    tr = await session.get(TaskRun, task_id)
    if tr is None or tr.kind != "ai.resume_tailor":
        raise HTTPException(404, "tarefa não encontrada")
    out: dict[str, Any] = {"status": tr.status, "progress": tr.progress, "message": tr.message, "error": tr.error}
    if tr.status == "done" and tr.entity_id:
        job = await session.get(Job, tr.entity_id)
        out["tailored"] = await service.tailored_info(session, job) if job else None
    return out


@router.post("/tailored/{variant_id}/approve")
async def approve_tailored(variant_id: uuid.UUID, session: SessionDep, _a: Auth) -> dict[str, Any]:
    """Você revisou e aprova o CV sob medida: ele passa a ser o anexado nesta vaga."""
    v = await session.get(ResumeVariant, variant_id)
    if v is None or v.kind != "tailored":
        raise HTTPException(404, "currículo não encontrado")
    if not (v.validation or {}).get("passed"):
        raise HTTPException(422, "o validador encontrou fatos fora do seu currículo: corrija em Currículos antes de aprovar")
    v.status = "approved"
    await audit.record(session, actor="user", action="resume.approve", entity_type="resume_variant", entity_id=v.id)
    await session.commit()
    job = await session.get(Job, v.job_id) if v.job_id else None
    return {"tailored": await service.tailored_info(session, job) if job else None}


@router.get("/tailored/{variant_id}/pdf")
async def tailored_pdf(variant_id: uuid.UUID, session: SessionDep, _a: Auth) -> Response:
    render = await resume_service.latest_render(session, variant_id)
    if render is None:
        raise HTTPException(404, "PDF ainda não gerado")
    return Response(get_storage().get(render.storage_key), media_type="application/pdf",
                    headers={"content-disposition": f'inline; filename="{render.filename}"'})  # fmt: skip


@router.get("/resume")
async def resume_for_job(session: SessionDep, _a: Auth, job_id: uuid.UUID | None = None) -> Response:
    """PDF para anexar no formulário: o CV da candidatura, senão o melhor coringa aprovado."""
    render: ResumeRender | None = None
    if job_id:
        job = await session.get(Job, job_id)
        if job is not None:
            app = await app_service.get_for_job(session, job.id)
            if app and app.resume_render_id:
                render = await session.get(ResumeRender, app.resume_render_id)
            if render is None:
                # CV sob medida desta vaga, se você aprovou
                tv = await service.tailored_variant(session, job)
                if tv is not None and tv.status == "approved":
                    render = await resume_service.latest_render(session, tv.id)
            if render is None:
                sel = await resume_service.select_for_job(session, job)
                if sel["best_variant_id"]:
                    render = await resume_service.latest_render(session, sel["best_variant_id"])
    if render is None:
        v = await session.scalar(
            select(ResumeVariant)
            .where(ResumeVariant.kind == "wildcard", ResumeVariant.status == "approved")
            .order_by(ResumeVariant.updated_at.desc())
            .limit(1)
        )
        render = await resume_service.latest_render(session, v.id) if v else None
    if render is None:
        raise HTTPException(404, "nenhum CV aprovado com PDF: gere em Currículos")
    return Response(
        get_storage().get(render.storage_key),
        media_type="application/pdf",
        headers={
            "content-disposition": f'attachment; filename="{render.filename}"',
            "x-filename": render.filename,
            "x-render-id": str(render.id),
            "access-control-expose-headers": "x-filename, x-render-id",
        },
    )


@router.get("/profile-summary")
async def profile_summary(session: SessionDep, _a: Auth) -> dict[str, Any]:
    _, master = await profile_service.current_master(session)
    return {"name": master.contact.full_name, "email": master.contact.email}
