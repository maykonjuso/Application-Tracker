"""Transparent completeness and consistency checks for collected job data."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from tracker.modules.jobs.models import Job, JobSnapshot, JobSource

CheckStatus = Literal["ok", "warning", "risk", "unknown"]

WEIGHTS = {
    "source": 15,
    "secure_link": 10,
    "company": 10,
    "description": 20,
    "recency": 15,
    "duplicates": 10,
    "integrity": 20,
}


def _check(key: str, label: str, status: CheckStatus, detail: str, *, scoreable: bool = True) -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "detail": detail, "scoreable": scoreable}


def assess_job_quality(
    job: Job,
    sources: list[JobSource],
    snapshots: list[JobSnapshot],
    *,
    chance_breakdown: dict[str, Any] | None = None,
    company_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assess data completeness/consistency; this is not a legitimacy or fraud verdict."""
    checks: list[dict[str, Any]] = []

    source_names = sorted({source.source for source in sources})
    checks.append(
        _check(
            "source",
            "Origem registrada",
            "ok" if sources else "risk",
            f"{len(sources)} registro(s) da(s) fonte(s) {', '.join(source_names)}. "
            "Isso permite rastrear de onde os dados chegaram, mas não autentica o publicador."
            if sources
            else "Nenhuma fonte de coleta foi registrada.",
        )
    )
    try:
        parsed_url = urlsplit(job.apply_url or job.canonical_url or "")
        has_valid_host = bool(parsed_url.hostname and not parsed_url.username and not parsed_url.password)
    except ValueError:
        parsed_url = urlsplit("")
        has_valid_host = False
    if not has_valid_host or parsed_url.scheme not in {"https", "http"}:
        checks.append(
            _check(
                "secure_link",
                "Link de candidatura",
                "risk",
                "Não há um endereço HTTP(S) válido sem credenciais embutidas. Isso impede abrir o link com segurança; "
                "um endereço válido, por si só, também não provaria que o domínio é oficial.",
            )
        )
    elif parsed_url.scheme == "https":
        checks.append(
            _check(
                "secure_link",
                "Link de candidatura",
                "ok",
                f"O endereço usa HTTPS e aponta para {parsed_url.hostname}. HTTPS protege a conexão com esse domínio, "
                "mas não confirma que o domínio pertence à empresa.",
            )
        )
    else:
        checks.append(
            _check(
                "secure_link",
                "Link de candidatura",
                "warning",
                f"O endereço aponta para {parsed_url.hostname}, mas usa HTTP sem criptografia. Isso não é prova de fraude; "
                "prefira localizar a vaga no site oficial.",
            )
        )

    company_present = bool(job.title.strip() and company_name and company_name.strip())
    checks.append(
        _check(
            "company",
            "Empresa e cargo identificados",
            "ok" if company_present else "risk",
            (
                f"Cargo “{job.title}” e empresa “{company_name}” estão preenchidos. "
                "A aplicação não comparou esse nome com o site oficial ou registros públicos."
                if company_present
                else "Falta identificar o cargo ou a empresa; sem esses dados, não é possível conferir a vaga."
            ),
        )
    )

    description_length = len((job.description_text or "").strip())
    if description_length >= 500:
        status: CheckStatus = "ok"
        detail = (
            f"Há {description_length} caracteres de descrição; o check considera suficiente a partir de 500. "
            "Isso mostra que há conteúdo para analisar, não que o conteúdo seja verdadeiro ou que a vaga esteja ativa."
        )
    elif description_length >= 150:
        status = "warning"
        detail = (
            f"A descrição tem {description_length} caracteres, abaixo do patamar de 500 usado como conteúdo suficiente. "
            "O intervalo de 150 a 499 é tratado como curto; confira se responsabilidades e requisitos aparecem no site de origem."
        )
    else:
        status = "risk"
        detail = (
            f"A descrição tem {description_length} caracteres (menos de 150), então faltam dados para avaliar responsabilidades "
            "e requisitos. É um sinal de baixa qualidade da informação, não uma prova de vaga falsa."
        )
    checks.append(_check("description", "Descrição suficiente", status, detail))

    posted_at = job.posted_at
    if posted_at is None:
        checks.append(
            _check(
                "recency",
                "Data de publicação",
                "unknown",
                "A fonte não forneceu uma data. A primeira vez que o sistema encontrou a vaga não prova "
                "quando ela foi publicada.",
            )
        )
    else:
        current = now or datetime.now(UTC)
        published = posted_at.replace(tzinfo=UTC) if posted_at.tzinfo is None else posted_at.astimezone(UTC)
        age_days = (current.astimezone(UTC) - published).total_seconds() / 86400
        if age_days < -1:
            status = "warning"
            detail = (
                f"A data informada resulta em {int(age_days)} dias em relação ao relógio atual. "
                "Data futura pode ser erro de fuso ou da fonte; confira o anúncio original."
            )
        elif age_days <= 30:
            status = "ok"
            detail = (
                f"A data informada indica publicação há {max(0, int(age_days))} dia(s), dentro da janela de 30 dias usada "
                "para considerar a informação recente. Isso não confirma que o processo seletivo ainda esteja aberto."
            )
        elif age_days <= 90:
            status = "warning"
            detail = (
                f"A data informada indica {int(age_days)} dias. Entre 31 e 90 dias, a vaga pede confirmação manual "
                "porque o anúncio pode continuar ativo ou já ter sido encerrado."
            )
        else:
            status = "risk"
            detail = (
                f"A data informada indica {int(age_days)} dias, acima do limite de 90 dias tratado como desatualizado. "
                "É um motivo para confirmar o status no site; não prova que a vaga seja fantasma."
            )
        checks.append(_check("recency", "Data de publicação", status, detail))

    repost_count = job.repost_count or 0
    if repost_count:
        checks.append(
            _check(
                "duplicates",
                "Repostagens",
                "warning",
                f"O sistema encontrou {repost_count} repostagem(ns) com outro identificador. O componente de vaga fantasma "
                "só aplica penalidade a partir de 2; repostagens podem ser republicações legítimas, mas vale verificar se "
                "a descrição e a data mudaram.",
            )
        )
    else:
        checks.append(
            _check(
                "duplicates",
                "Repostagens",
                "ok",
                "Nenhuma repostagem foi detectada para esta vaga até agora. Isso só significa que o sistema não observou "
                "uma repetição correspondente; não garante que não exista republicação em outra fonte ou com outros dados.",
            )
        )

    if not snapshots:
        checks.append(
            _check(
                "integrity",
                "Integridade do texto coletado",
                "unknown",
                "Não há captura original para comparar o texto. Portanto, a aplicação não consegue dizer se ele "
                "mudou desde a coleta.",
            )
        )
    else:
        original_hash = snapshots[-1].sha256
        current_hash = hashlib.sha256((job.description_text or "").encode()).hexdigest()
        matches = original_hash == current_hash
        checks.append(
            _check(
                "integrity",
                "Integridade do texto coletado",
                "ok" if matches else "risk",
                "O SHA-256 do texto atual corresponde ao da última captura guardada. Isso detecta divergência após a captura, "
                "mas não autentica a fonte nem prova que a captura original era legítima."
                if matches
                else "O SHA-256 do texto atual difere do da última captura guardada. O texto pode ter sido "
                "atualizado ou alterado; "
                "compare com o anúncio original antes de confiar nos detalhes.",
            )
        )

    ghost_signal = (chance_breakdown or {}).get("nao_fantasma")
    if isinstance(ghost_signal, dict):
        ghost_value = ghost_signal.get("value")
        ghost_missing = bool(ghost_signal.get("missing"))
        ghost_evidence = str(ghost_signal.get("evidence") or "O motor não forneceu detalhes dos sinais.")
        if isinstance(ghost_value, (int, float)) and ghost_value < 1:
            ghost_status: CheckStatus = "warning"
            ghost_prefix = f"Pontuação heurística do componente: {round(ghost_value * 100)}% (não é probabilidade). "
            ghost_explanation = "Um ou mais sinais usados pelo componente reduziram a pontuação. "
        elif ghost_missing:
            ghost_status = "unknown"
            ghost_prefix = "Os dados disponíveis são parciais. "
            ghost_explanation = "Um ou mais fatores não têm dados suficientes para uma conclusão completa. "
        else:
            ghost_status = "ok"
            ghost_prefix = "Nenhum dos fatores conhecidos reduziu este componente. "
            ghost_explanation = "Isto não confirma que a vaga seja real, legítima ou ainda esteja aberta. "
        checks.append(
            _check(
                "ghost_job",
                "Sinais associados a vaga fantasma",
                ghost_status,
                ghost_prefix
                + ghost_explanation
                + ghost_evidence
                + " Quando a data de publicação falta, a antiguidade desse componente usa a primeira coleta. "
                "O cálculo considera repostagem a partir de 2 ocorrências, anúncio acima de 60 dias, descrição genérica "
                "e informação de demissões recentes; estes indícios são indiretos e não verificam a existência de uma vaga real.",
                scoreable=False,
            )
        )
    else:
        checks.append(
            _check(
                "ghost_job",
                "Sinais associados a vaga fantasma",
                "unknown",
                "O motor ainda não calculou esse componente. Sem os dados de repostagem, antiguidade, descrição genérica e "
                "contexto recente da empresa, não há base para dizer por que a vaga parece ou não fantasma.",
                scoreable=False,
            )
        )

    checks.append(
        _check(
            "official_verification",
            "Confirmação no site oficial",
            "unknown",
            "A aplicação não confirma se a empresa publicou esta vaga no próprio site.",
            scoreable=False,
        )
    )

    scam_signals = (job.extra or {}).get("scam_signals") or []
    if scam_signals:
        checks.append(
            _check(
                "scam_signals",
                "Sinais suspeitos (análise automática)",
                "risk",
                "Possíveis sinais identificados: " + "; ".join(str(signal) for signal in scam_signals[:5]),
                scoreable=False,
            )
        )
    elif (job.extra or {}).get("needs_extraction"):
        checks.append(
            _check(
                "scam_signals",
                "Sinais suspeitos (análise automática)",
                "unknown",
                "A análise automática ainda não foi concluída; ausência de alertas não significa que a vaga é segura.",
                scoreable=False,
            )
        )
    else:
        checks.append(
            _check(
                "scam_signals",
                "Sinais suspeitos (análise automática)",
                "ok",
                "Nenhum sinal foi identificado pela análise automática. Isso não comprova que a vaga é segura.",
                scoreable=False,
            )
        )

    earned = possible = 0.0
    for check in checks:
        key = check["key"]
        if not check["scoreable"] or key not in WEIGHTS:
            continue
        weight = WEIGHTS[key]
        possible += weight
        earned += weight * {"ok": 1.0, "warning": 0.5, "risk": 0.0, "unknown": 0.0}[check["status"]]
    score = round(earned / possible * 100) if possible else 0
    if any(check["key"] == "scam_signals" and check["status"] == "risk" for check in checks):
        summary = "Sinais suspeitos exigem revisão"
    elif any(check["status"] == "risk" for check in checks):
        summary = "Verificações importantes pendentes"
    elif any(check["scoreable"] and check["status"] == "unknown" for check in checks):
        summary = "Dados incompletos"
    else:
        summary = "Consistência alta" if score >= 80 else "Requer conferência" if score >= 55 else "Informação insuficiente"
    return {
        "score": score,
        "summary": summary,
        "score_explanation": "Mede completude e consistência dos dados, não a legitimidade da empresa ou da vaga. "
        "Cada verificação explica o dado usado e o que ele não permite concluir.",
        "checks": checks,
    }
