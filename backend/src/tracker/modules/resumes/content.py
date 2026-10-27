"""Montagem do conteúdo renderizável a partir do mestre (determinístico) e de rascunhos de IA."""

from __future__ import annotations

import re

from tracker.modules.profile import facts
from tracker.modules.profile.schemas import Experience, MasterProfile
from tracker.modules.resumes.schemas import (
    CertBlock,
    EduBlock,
    ExpBlock,
    LangBlock,
    ProjBlock,
    ResumeContent,
    ResumeDraft,
    SkillRow,
)

CATEGORY_LABELS = {
    "languages": ("Languages", "Linguagens"),
    "frontend": ("Frontend", "Frontend"),
    "backend_and_web": ("Backend and Web", "Backend e Web"),
    "mobile": ("Mobile", "Mobile"),
    "data_engineering": ("Data Engineering", "Engenharia de Dados"),
    "data_quality": ("Data Quality", "Qualidade de Dados"),
    "data_science": ("Data Science", "Ciência de Dados"),
    "ml_and_llm": ("ML and LLM", "ML e LLM"),
    "databases": ("Databases", "Bancos de Dados"),
    "devops_and_cloud": ("DevOps and Cloud", "DevOps e Cloud"),
    "observability": ("Observability", "Observabilidade"),
    "infrastructure": ("Infrastructure", "Infraestrutura"),
    "testing": ("Testing", "Testes"),
    "bi_and_automation": ("BI and Automation", "BI e Automação"),
    "game_dev": ("Game Development", "Desenvolvimento de Jogos"),
    "practices": ("Practices", "Práticas"),
}


def category_key(label: str) -> str:
    """'Data Engineering' / 'Engenharia de Dados' / 'data_engineering' → chave canônica (snake_case)."""
    import unicodedata

    t = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    for key, (en, pt) in CATEGORY_LABELS.items():
        if t in {key, category_key_plain(en), category_key_plain(pt)}:
            return key
    return t or "other"


def category_key_plain(text: str) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


# Perfis coringa (docs/11 §4): foco e ordem de ênfase das categorias de skill. Categorias que o seu currículo
# tiver e não estiverem na lista entram no fim, então nenhum perfil sai sem as suas competências.
WILDCARD_PROFILES: dict[str, dict[str, object]] = {
    "data_engineer": {
        "name": "Data Engineer",
        "headline_key": "data_engineer",
        "focus": "pipelines ETL/ELT em escala, batch e streaming, modelagem, qualidade de dados, cloud",
        "categories": ["languages", "data_engineering", "data_quality", "databases", "devops_and_cloud",
                       "ml_and_llm", "observability", "infrastructure", "practices"],
    },
    "ai_ml_engineer": {
        "name": "AI ML Engineer",
        "headline_key": "ai_engineer",
        "focus": "modelos e LLMs em produção, RAG, agentes, MLOps, avaliação de modelos",
        "categories": ["languages", "ml_and_llm", "data_science", "data_engineering", "backend_and_web",
                       "databases", "devops_and_cloud", "observability", "practices"],
    },
    "data_scientist": {
        "name": "Data Scientist",
        "headline_key": "data_scientist",
        "focus": "análise, estatística, modelagem preditiva, experimentação, comunicação de resultados",
        "categories": ["languages", "data_science", "ml_and_llm", "databases", "bi_and_automation",
                       "data_engineering", "practices"],
    },
    "software_engineer_backend": {
        "name": "Software Engineer Backend",
        "headline_key": "software_engineer",
        "focus": "APIs, sistemas distribuídos, integrações, mensageria, desempenho e confiabilidade",
        "categories": ["languages", "backend_and_web", "databases", "devops_and_cloud", "testing",
                       "observability", "data_engineering", "practices"],
    },
    "frontend": {
        "name": "Frontend Engineer",
        "headline_key": "software_engineer",
        "focus": "interfaces web, React/Angular/Vue, acessibilidade, desempenho no navegador, design systems",
        "categories": ["languages", "frontend", "testing", "backend_and_web", "devops_and_cloud", "practices"],
    },
    "fullstack": {
        "name": "Full-Stack Engineer",
        "headline_key": "software_engineer",
        "focus": "produto ponta a ponta: frontend, APIs, banco de dados e deploy",
        "categories": ["languages", "frontend", "backend_and_web", "databases", "devops_and_cloud", "testing",
                       "practices"],
    },
    "mobile": {
        "name": "Mobile Engineer",
        "headline_key": "software_engineer",
        "focus": "apps Android/iOS nativos ou multiplataforma, publicação nas lojas, desempenho",
        "categories": ["languages", "mobile", "frontend", "backend_and_web", "testing", "practices"],
    },
    "devops_sre": {
        "name": "DevOps SRE",
        "headline_key": "devops_engineer",
        "focus": "infraestrutura como código, CI/CD, Kubernetes, observabilidade, confiabilidade",
        "categories": ["languages", "devops_and_cloud", "infrastructure", "observability", "databases", "practices"],
    },
}  # fmt: skip

LABELS = {
    "en": {
        "summary": "Summary",
        "skills": "Technical Skills",
        "experience": "Professional Experience",
        "projects": "Featured Projects",
        "education": "Education",
        "certifications": "Certifications",
        "languages": "Languages",
    },
    "pt": {
        "summary": "Resumo",
        "skills": "Competências Técnicas",
        "experience": "Experiência Profissional",
        "projects": "Projetos em Destaque",
        "education": "Formação",
        "certifications": "Certificações",
        "languages": "Idiomas",
    },
}


def org_line(exp: Experience, lang: str) -> str:
    line = exp.org_line_en if lang == "en" else exp.org_line_pt
    return line or exp.organization_short or exp.organization


def bullets_for(exp: Experience, lang: str) -> list[str]:
    if lang == "pt" and exp.bullets_pt:
        return exp.bullets_pt
    return exp.bullets_en


def _category_label(key: str, lang: str) -> str:
    en, pt = CATEGORY_LABELS.get(key, (key.replace("_", " ").title(), key.replace("_", " ").title()))
    return en if lang == "en" else pt


def _common_blocks(master: MasterProfile, lang: str) -> dict[str, list]:
    education = [
        EduBlock(
            degree=e.degree,
            period=facts.format_period(e.start, e.end, lang),
            institution=e.institution + (f", {e.location}" if e.location else ""),
        )
        for e in master.education
        if e.show_by_default
    ]
    certs = [CertBlock(name=c.name, issuer=c.issuer) for c in master.certifications if c.show_by_default]
    langs = [LangBlock(name=lang_.name, level=lang_.level) for lang_ in master.languages if lang_.level]
    return {"education": education, "certifications": certs, "languages": langs}


def baseline(master: MasterProfile, profile: str = "data_engineer", lang: str = "en") -> ResumeContent:
    """Conteúdo sem IA: tudo do mestre, na ordem do mestre."""
    spec = WILDCARD_PROFILES.get(profile, WILDCARD_PROFILES["data_engineer"])
    preferred: list[str] = list(spec["categories"])  # type: ignore[arg-type]
    by_key = {category_key(k): (k, v) for k, v in master.skills.items() if v}
    order = [k for k in preferred if k in by_key] + [k for k in by_key if k not in preferred]
    rows = [SkillRow(category=_category_label(k, lang), items=by_key[k][1]) for k in order]
    headline_key = str(spec["headline_key"])
    headline = master.headlines.get(headline_key) or next(iter(master.headlines.values()), "")
    summary = (
        master.summaries.get(f"{profile}_{lang}")
        or master.summaries.get(f"{profile}_en")
        or next(iter(master.summaries.values()), headline)
    )
    exps = [
        ExpBlock(
            experience_id=e.id,
            title=e.title,
            organization=org_line(e, lang),
            location=e.location,
            period=facts.format_period(e.start, e.end, lang),
            bullets=bullets_for(e, lang),
        )
        for e in master.experience
    ]
    projects = [
        ProjBlock(
            project_id=p.id,
            name=p.name,
            url=p.url,
            description=(p.description_pt if lang == "pt" and p.description_pt else p.description_en),
        )
        for p in master.projects
    ]
    return ResumeContent(
        language=lang,
        headline=headline,
        summary=summary,
        skill_rows=rows,
        experience=exps,
        projects=projects,
        **_common_blocks(master, lang),
    )


def from_draft(master: MasterProfile, draft: ResumeDraft, lang: str) -> ResumeContent:
    """Monta o conteúdo a partir do rascunho da IA: empresa, local e datas vêm sempre do mestre."""
    exps: list[ExpBlock] = []
    for d in draft.experiences:
        e = master.experience_by_id(d.experience_id)
        if e is None:
            exps.append(ExpBlock(experience_id=d.experience_id, title=d.title, organization="?", period="?", bullets=d.bullets))
            continue
        exps.append(
            ExpBlock(
                experience_id=e.id,
                title=d.title,
                organization=org_line(e, lang),
                location=e.location,
                period=facts.format_period(e.start, e.end, lang),
                bullets=d.bullets,
            )
        )
    # a ordem das experiências é a do currículo mestre (a IA escolhe o texto, não a ordem)
    rank = {e.id: i for i, e in enumerate(master.experience)}
    exps.sort(key=lambda x: rank.get(x.experience_id, len(rank)))
    by_id = {p.id: p for p in master.projects}
    projects = [
        ProjBlock(
            project_id=pid,
            name=by_id[pid].name,
            url=by_id[pid].url,
            description=((by_id[pid].description_pt or by_id[pid].description_en) if lang == "pt" else by_id[pid].description_en),
        )
        for pid in draft.project_ids
        if pid in by_id
    ]
    return ResumeContent(
        language=lang,
        headline=draft.headline,
        summary=draft.summary,
        skill_rows=draft.skill_rows,
        experience=exps,
        projects=projects,
        **_common_blocks(master, lang),
    )


def content_text(c: ResumeContent) -> str:
    """Texto plano do conteúdo (embeddings, cobertura de keywords)."""
    parts = [c.headline, c.summary]
    parts += [", ".join(r.items) for r in c.skill_rows]
    for e in c.experience:
        parts.append(e.title)
        parts += e.bullets
    parts += [p.description for p in c.projects]
    return "\n".join(parts).replace("**", "")
