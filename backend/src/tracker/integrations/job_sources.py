"""Fontes de vagas com API pública (docs/07 §3): Greenhouse, Lever, Ashby, Gupy."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

import httpx

from tracker.modules.jobs.normalize import RawJob, html_to_text, parse_date

UA = {"User-Agent": "ApplicationTracker/0.1 (uso pessoal)"}


def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30, headers=UA, transport=transport, follow_redirects=True)


def _dt_ms(v: Any) -> datetime | None:
    """Data da fonte → UTC. Aceita epoch (s ou ms), ISO, RFC 2822 (RSS) e texto relativo."""
    return parse_date(v)


async def greenhouse(token: str, company_name: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    async with _client(transport) as c:
        r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
        r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        content = j.get("content") or ""
        import html as _h

        html_desc = _h.unescape(content)
        out.append(
            RawJob(
                source="greenhouse",
                external_id=str(j["id"]),
                title=j["title"],
                company_name=company_name,
                url=j.get("absolute_url"),
                location=(j.get("location") or {}).get("name"),
                description_html=html_desc,
                description_text=html_to_text(html_desc),
                posted_at=_dt_ms(j.get("first_published")),
                apply_url=j.get("absolute_url"),
                apply_kind="external",
                ats_kind="greenhouse",
                raw=j,
            )
        )
    return out


async def greenhouse_questions(
    token: str, job_id: str, transport: httpx.AsyncBaseTransport | None = None
) -> list[dict[str, Any]]:
    async with _client(transport) as c:
        r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}", params={"questions": "true"})
        r.raise_for_status()
    qs = []
    for q in r.json().get("questions", []):
        fields = q.get("fields") or [{}]
        f = fields[0]
        qs.append(
            {
                "label": q.get("label"),
                "required": q.get("required", False),
                "type": f.get("type"),
                "options": [v.get("label") for v in f.get("values", [])],
            }
        )
    return qs


async def lever(company: str, company_name: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    async with _client(transport) as c:
        r = await c.get(f"https://api.lever.co/v0/postings/{company}", params={"mode": "json"})
        r.raise_for_status()
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        parts = [j.get("descriptionPlain") or ""]
        for lst in j.get("lists", []):
            parts.append(f"{lst.get('text')}\n{html_to_text(lst.get('content', ''))}")
        parts.append(j.get("additionalPlain") or "")
        sal = j.get("salaryRange")
        out.append(
            RawJob(
                source="lever",
                external_id=j["id"],
                title=j["text"],
                company_name=company_name,
                url=j.get("hostedUrl"),
                location=cats.get("location"),
                description_text="\n\n".join(p for p in parts if p).strip(),
                posted_at=_dt_ms(j.get("createdAt")),
                apply_url=j.get("applyUrl") or j.get("hostedUrl"),
                apply_kind="external",
                ats_kind="lever",
                work_model={"remote": "remote", "hybrid": "hybrid", "on-site": "onsite"}.get(
                    (j.get("workplaceType") or "").lower()
                ),
                salary={
                    "min": sal.get("min"),
                    "max": sal.get("max"),
                    "currency": sal.get("currency"),
                    "period": "year" if sal.get("interval", "").startswith("per-year") else "month",
                    "estimated": False,
                }
                if sal
                else None,
                raw=j,
            )
        )
    return out


async def ashby(board: str, company_name: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    async with _client(transport) as c:
        r = await c.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}", params={"includeCompensation": "true"})
        r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = (j.get("compensation") or {}).get("summaryComponents") or []
        salary = None
        for comp_item in comp:
            if comp_item.get("compensationType") == "Salary":
                salary = {
                    "min": comp_item.get("minValue"),
                    "max": comp_item.get("maxValue"),
                    "currency": comp_item.get("currencyCode"),
                    "period": "year" if comp_item.get("interval") == "1 YEAR" else "month",
                    "estimated": False,
                }
        wm = "remote" if j.get("isRemote") else None
        out.append(
            RawJob(
                source="ashby",
                external_id=j["id"],
                title=j["title"],
                company_name=company_name,
                url=j.get("jobUrl"),
                location=j.get("location"),
                description_html=j.get("descriptionHtml"),
                description_text=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or ""),
                posted_at=_dt_ms(j.get("publishedAt")),
                apply_url=j.get("applyUrl") or j.get("jobUrl"),
                apply_kind="external",
                ats_kind="ashby",
                work_model=wm,
                salary=salary,
                raw=j,
            )
        )
    return out


async def gupy(subdomain: str, company_name: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    """Página pública de vagas da Gupy (dados JSON embutidos no HTML, __NEXT_DATA__)."""
    import json

    async with _client(transport) as c:
        r = await c.get(f"https://{subdomain}.gupy.io/")
        r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        return []
    data = json.loads(m.group(1))
    jobs = (data.get("props", {}).get("pageProps", {}).get("jobs")) or []
    out = []
    for j in jobs:
        jid = str(j.get("id"))
        url = f"https://{subdomain}.gupy.io/jobs/{jid}"
        wp_raw = j.get("workplace")
        wp: dict[str, Any] = wp_raw if isinstance(wp_raw, dict) else {}
        addr_raw = wp.get("address")
        addr: dict[str, Any] = addr_raw if isinstance(addr_raw, dict) else {}
        wtype = str(j.get("workplaceType") or wp.get("workplaceType") or "").lower()
        wm = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite", "presencial": "onsite"}.get(wtype)
        city, state = j.get("city") or addr.get("city"), j.get("state") or addr.get("stateShortName") or addr.get("state")
        out.append(
            RawJob(
                source="gupy",
                external_id=jid,
                title=j.get("title") or j.get("name") or "",
                company_name=company_name,
                url=url,
                location=", ".join(filter(None, [city, state])) or None,
                description_text=html_to_text(j.get("description") or ""),
                posted_at=_dt_ms(j.get("publishedDate") or j.get("publishedAt")),
                apply_url=url,
                apply_kind="external",
                ats_kind="gupy",
                work_model=wm,
                raw=j,
            )
        )
    return out


async def gupy_job_detail(
    subdomain: str, job_id: str, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any] | None:
    """Página da vaga na Gupy: a listagem não traz data nem descrição, a página traz."""
    import json

    async with _client(transport) as c:
        r = await c.get(f"https://{subdomain}.gupy.io/jobs/{job_id}")
        r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        return None
    job = json.loads(m.group(1)).get("props", {}).get("pageProps", {}).get("job")
    return job if isinstance(job, dict) else None


GUPY_SECTIONS = (
    ("description", ""),
    ("responsibilities", "Responsabilidades e atribuições"),
    ("prerequisites", "Requisitos e qualificações"),
    ("relevantExperiences", "Diferenciais"),
)


async def gupy_enrich(
    subdomain: str,
    raws: list[RawJob],
    transport: httpx.AsyncBaseTransport | None = None,
    concurrency: int = 4,
) -> int:
    """Completa data de publicação e descrição das vagas Gupy (uma página por vaga). Devolve quantas completou."""
    sem = asyncio.Semaphore(concurrency)

    async def one(raw: RawJob) -> bool:
        async with sem:
            try:
                job = await gupy_job_detail(subdomain, raw.external_id, transport)
            except (httpx.HTTPError, ValueError):
                return False  # sem a página, a vaga entra mesmo assim (data = quando a encontramos)
        if not job:
            return False
        raw.posted_at = raw.posted_at or _dt_ms(job.get("publishedAt"))
        parts = [f"<h3>{title}</h3>{job[k]}" if title else str(job[k]) for k, title in GUPY_SECTIONS if job.get(k)]
        if parts and not raw.description_text:
            raw.description_html = "\n".join(parts)
            raw.description_text = html_to_text(raw.description_html)
        return True

    return sum(await asyncio.gather(*(one(r) for r in raws)))


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "gupy": gupy}


def parse_ats_url(url: str) -> tuple[str, str, str | None] | None:
    """URL de vaga → (ats, board_token, job_id)."""
    patterns = [
        ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)(?:/jobs/(\d+))?"),
        ("lever", r"jobs\.lever\.co/([\w-]+)(?:/([\w-]+))?"),
        ("ashby", r"jobs\.ashbyhq\.com/([\w%.-]+)(?:/([\w-]+))?"),
        ("gupy", r"([\w-]+)\.gupy\.io(?:/jobs/(\d+))?"),
    ]
    for ats, p in patterns:
        m = re.search(p, url)
        if m:
            return ats, m.group(1), m.group(2)
    return None


async def smartrecruiters(
    company: str,
    company_name: str,
    transport: httpx.AsyncBaseTransport | None = None,
    query: str | None = None,
    max_items: int = 300,
) -> list[RawJob]:
    out: list[RawJob] = []
    async with _client(transport) as c:
        offset = 0
        while offset < max_items:
            params: dict[str, Any] = {"limit": 100, "offset": offset}
            if query:
                params["q"] = query
            r = await c.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings", params=params)
            r.raise_for_status()
            data = r.json()
            for j in data.get("content", []):
                loc = j.get("location") or {}
                wm = "remote" if loc.get("remote") else "hybrid" if loc.get("hybrid") else None
                url = f"https://jobs.smartrecruiters.com/{company}/{j['id']}"
                out.append(
                    RawJob(
                        source="smartrecruiters",
                        external_id=str(j["id"]),
                        title=j["name"],
                        company_name=company_name,
                        url=url,
                        location=loc.get("fullLocation"),
                        description_text=j.get("name", ""),
                        posted_at=_dt_ms(j.get("releasedDate")),
                        apply_url=url,
                        apply_kind="external",
                        ats_kind="smartrecruiters",
                        work_model=wm,
                        raw={"id": j["id"]},
                    )
                )
            offset += 100
            if offset >= int(data.get("totalFound", 0)):
                break
    return out


async def workable(account: str, company_name: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    async with _client(transport) as c:
        r = await c.get(f"https://apply.workable.com/api/v1/widget/accounts/{account}")
        r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = ", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")])) or None
        out.append(
            RawJob(
                source="workable",
                external_id=str(j.get("shortcode") or j.get("code")),
                title=j["title"],
                company_name=company_name,
                url=j.get("url"),
                location=loc,
                description_text=" ".join(
                    filter(None, [j.get("title"), j.get("department"), j.get("function"), j.get("experience")])
                ),
                posted_at=_dt_ms(j.get("published_on") or j.get("created_at")),
                apply_url=j.get("application_url") or j.get("url"),
                apply_kind="external",
                ats_kind="workable",
                work_model="remote" if j.get("telecommuting") else None,
                raw=j,
            )
        )
    return out


FETCHERS.update({"smartrecruiters": smartrecruiters, "workable": workable})


# ---------------- Agregadores (APIs/feeds públicos de vagas) ----------------
async def _get_json(url: str, params: dict[str, Any] | None = None, transport: httpx.AsyncBaseTransport | None = None) -> Any:
    async with _client(transport) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def board_remotive(query: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    data = await _get_json("https://remotive.com/api/remote-jobs", {"search": query, "limit": 100}, transport)
    return [
        RawJob(
            source="remotive",
            external_id=str(j["id"]),
            title=j["title"],
            company_name=j["company_name"],
            url=j["url"],
            location=j.get("candidate_required_location"),
            description_html=j.get("description"),
            description_text=html_to_text(j.get("description") or ""),
            posted_at=_dt_ms(j.get("publication_date")),
            apply_url=j["url"],
            apply_kind="external",
            work_model="remote",
            raw={"tags": j.get("tags"), "salary": j.get("salary")},
        )
        for j in data.get("jobs", [])
    ]


async def board_remoteok(query: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    tag = query.split("|")[0].strip().split()[0].lower() if query.strip() else ""
    data = await _get_json("https://remoteok.com/api", {"tag": tag} if tag else None, transport)
    out = []
    for j in data[1:] if isinstance(data, list) else []:
        if not j.get("id"):
            continue
        out.append(
            RawJob(
                source="remoteok",
                external_id=str(j["id"]),
                title=j.get("position", ""),
                company_name=j.get("company", ""),
                url=j.get("url"),
                location=j.get("location"),
                description_html=j.get("description"),
                description_text=html_to_text(j.get("description") or ""),
                posted_at=_dt_ms(j.get("date") or j.get("epoch")),
                apply_url=j.get("apply_url") or j.get("url"),
                apply_kind="external",
                work_model="remote",
                salary={
                    "min": j.get("salary_min"),
                    "max": j.get("salary_max"),
                    "currency": "USD",
                    "period": "year",
                    "estimated": False,
                }
                if j.get("salary_max")
                else None,
                raw={"tags": j.get("tags")},
            )
        )
    return out


async def board_himalayas(query: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    data = await _get_json("https://himalayas.app/jobs/api/search", {"q": query, "limit": 50}, transport)
    out = []
    for j in data.get("jobs", []):
        out.append(
            RawJob(
                source="himalayas",
                external_id=str(j.get("guid") or j.get("applicationLink")),
                title=j["title"],
                company_name=j.get("companyName", ""),
                url=j.get("applicationLink"),
                location=", ".join(j.get("locationRestrictions") or []) or "Remote",
                description_html=j.get("description"),
                description_text=html_to_text(j.get("description") or j.get("excerpt") or ""),
                posted_at=_dt_ms(j.get("pubDate")),
                apply_url=j.get("applicationLink"),
                apply_kind="external",
                work_model="remote",
                salary={
                    "min": j.get("minSalary"),
                    "max": j.get("maxSalary"),
                    "currency": j.get("currency"),
                    "period": "year" if j.get("salaryPeriod") == "annual" else "month",
                    "estimated": False,
                }
                if j.get("maxSalary")
                else None,
                raw={"seniority": j.get("seniority")},
            )
        )
    return out


async def board_jobicy(query: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    tag = query.split("|")[0].strip()
    data = await _get_json("https://jobicy.com/api/v2/remote-jobs", {"count": 50, "tag": tag}, transport)
    return [
        RawJob(
            source="jobicy",
            external_id=str(j["id"]),
            title=j["jobTitle"],
            company_name=j["companyName"],
            url=j["url"],
            location=j.get("jobGeo"),
            description_html=j.get("jobDescription"),
            description_text=html_to_text(j.get("jobDescription") or j.get("jobExcerpt") or ""),
            posted_at=_dt_ms(j.get("pubDate")),
            apply_url=j["url"],
            apply_kind="external",
            work_model="remote",
            raw={"level": j.get("jobLevel")},
        )
        for j in data.get("jobs", [])
    ]


async def board_arbeitnow(query: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    data = await _get_json("https://www.arbeitnow.com/api/job-board-api", None, transport)
    return [
        RawJob(
            source="arbeitnow",
            external_id=j["slug"],
            title=j["title"],
            company_name=j["company_name"],
            url=j["url"],
            location=j.get("location"),
            description_html=j.get("description"),
            description_text=html_to_text(j.get("description") or ""),
            posted_at=_dt_ms(j.get("created_at")),
            apply_url=j["url"],
            apply_kind="external",
            work_model="remote" if j.get("remote") else None,
            raw={"tags": j.get("tags")},
        )
        for j in data.get("data", [])
    ]


async def board_workingnomads(query: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    data = await _get_json("https://www.workingnomads.com/api/exposed_jobs/", None, transport)
    return [
        RawJob(
            source="workingnomads",
            external_id=j["url"],
            title=j["title"],
            company_name=j.get("company_name", ""),
            url=j["url"],
            location=j.get("location"),
            description_html=j.get("description"),
            description_text=html_to_text(j.get("description") or ""),
            posted_at=_dt_ms(j.get("pub_date")),
            apply_url=j["url"],
            apply_kind="external",
            work_model="remote",
            raw={"tags": j.get("tags")},
        )
        for j in (data if isinstance(data, list) else [])
    ]


async def board_weworkremotely(query: str, transport: httpx.AsyncBaseTransport | None = None) -> list[RawJob]:
    import xml.etree.ElementTree as ET

    async with _client(transport) as c:
        r = await c.get("https://weworkremotely.com/categories/remote-programming-jobs.rss")
        r.raise_for_status()
    root = ET.fromstring(r.text)  # noqa: S314 (feed público, sem DTD externo)
    out = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        company, _, role = title.partition(": ")
        link = item.findtext("link") or ""
        desc = item.findtext("description") or ""
        out.append(
            RawJob(
                source="weworkremotely",
                external_id=link,
                title=role or title,
                company_name=company or "WWR",
                url=link,
                location=item.findtext("region"),
                description_html=desc,
                description_text=html_to_text(desc),
                posted_at=_dt_ms(item.findtext("pubDate")),
                apply_url=link,
                apply_kind="external",
                work_model="remote",
            )
        )
    return out


BOARDS = {
    "remotive": board_remotive,
    "remoteok": board_remoteok,
    "himalayas": board_himalayas,
    "jobicy": board_jobicy,
    "arbeitnow": board_arbeitnow,
    "workingnomads": board_workingnomads,
    "weworkremotely": board_weworkremotely,
}
