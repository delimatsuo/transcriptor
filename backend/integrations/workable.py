"""Workable SPI v3 client and candidate dossier integration for Transcriptor.

Provides direct access to Workable's candidate profiles, job descriptions,
and interview activity streams (notes, evaluations, ratings) to automatically
populate Transcriptor's executive interview context slots and export final reports.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

WORKABLE_SPI_BASE_TEMPLATE = "https://{subdomain}.workable.com/spi/v3"


class WorkableError(Exception):
    """Base exception for Workable integration errors."""


class WorkableConfigurationError(WorkableError):
    """Raised when Workable integration is misconfigured or credentials are missing."""


class WorkableNotFoundError(WorkableError):
    """Raised when a candidate or job is not found on Workable."""


class WorkableAuthError(WorkableError):
    """Raised when Workable rejects the API token or access permissions."""


class WorkableRateLimitError(WorkableError):
    """Raised when Workable rate limits requests (HTTP 429)."""


class WorkableCandidateDossier(BaseModel):
    """Structured candidate and job briefing assembled from Workable."""

    candidate_id: str = Field(description="Workable candidate ID (e.g. c12345)")
    candidate_name: str = Field(description="Full candidate name")
    job_shortcode: str | None = Field(default=None, description="Job shortcode if linked")
    job_title: str | None = Field(default=None, description="Job title if linked")
    candidate_email: str | None = Field(default=None, description="Candidate email address")
    current_stage: str | None = Field(default=None, description="Current pipeline stage name")
    jd_text: str = Field(default="", description="Formatted job description context")
    cv_text: str = Field(default="", description="Formatted candidate CV and background")
    briefing_text: str = Field(default="", description="Formatted pre-interview recruiter briefing")
    raw_candidate: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw candidate metadata from Workable API",
    )


class WorkableEvent(BaseModel):
    """Scheduled interview or call event from Workable."""

    id: str = Field(description="Event unique identifier")
    title: str = Field(description="Event title / subject")
    event_type: str = Field(default="interview", description="Event type (call, interview, meeting)")
    starts_at: str = Field(description="Event start time (ISO 8601)")
    ends_at: str | None = Field(default=None, description="Event end time (ISO 8601)")
    cancelled: bool = Field(default=False, description="Whether event was cancelled")
    candidate_id: str | None = Field(default=None, description="Linked candidate ID")
    candidate_name: str | None = Field(default=None, description="Linked candidate name")
    job_shortcode: str | None = Field(default=None, description="Linked job shortcode")
    job_title: str | None = Field(default=None, description="Linked job title")
    conference_url: str | None = Field(default=None, description="Meeting video link (Google Meet, Zoom, etc.)")
    interviewers: list[str] = Field(default_factory=list, description="Names of participating interviewers")


def strip_html(raw_html: str | None) -> str:
    """Safely convert HTML formatting (from Workable descriptions/notes) into clean plain text/markdown."""
    if not raw_html:
        return ""

    text = raw_html

    # Replace breaks and paragraph tags with newlines
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?p[^>]*>", "\n\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n• ", text)
    text = re.sub(r"(?i)</?ul[^>]*>", "\n", text)
    text = re.sub(r"(?i)</?ol[^>]*>", "\n", text)

    # Convert headers
    text = re.sub(r"(?i)<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n\n### \1\n", text)

    # Convert bold / strong
    text = re.sub(r"(?i)<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>", r"**\1**", text)

    # Convert italics / em
    text = re.sub(r"(?i)<(?:em|i)[^>]*>(.*?)</(?:em|i)>", r"_\1_", text)

    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", "", text)

    # Unescape HTML entities (&amp;, &lt;, &gt;, &quot;, &#39;, etc.)
    text = html.unescape(text)

    # Normalize excessive newlines (max 2 consecutive) and trailing whitespace
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def parse_workable_candidate_input(url_or_id: str) -> tuple[str, str | None]:
    """Parse a Workable candidate URL or raw ID into (candidate_id, optional_job_shortcode).

    Supports formats:
    - https://{subdomain}.workable.com/backend/jobs/{shortcode}/candidates/{candidate_id}
    - https://{subdomain}.workable.com/backend/jobs/{job_id}/candidates/{candidate_id}
    - https://{subdomain}.workable.com/candidates/{candidate_id}
    - https://app.workable.com/candidates/{candidate_id}
    - c12345 / 12345 (raw ID)
    """
    raw = url_or_id.strip()
    if not raw:
        raise ValueError("URL ou ID do candidato no Workable não pode estar em branco")

    # If it's a URL
    if raw.startswith("http://") or raw.startswith("https://") or "workable.com" in raw:
        if not (raw.startswith("http://") or raw.startswith("https://")):
            raw = f"https://{raw}"

        parsed = urlparse(raw)
        path = parsed.path.strip("/")
        segments = path.split("/")

        # Pattern: .../jobs/{shortcode}/candidates/{candidate_id}
        if "jobs" in segments and "candidates" in segments:
            job_idx = segments.index("jobs")
            cand_idx = segments.index("candidates")
            shortcode = segments[job_idx + 1] if job_idx + 1 < len(segments) else None
            candidate_id = segments[cand_idx + 1] if cand_idx + 1 < len(segments) else None
            if candidate_id:
                return candidate_id, shortcode

        # Pattern: .../candidates/{candidate_id}
        if "candidates" in segments:
            cand_idx = segments.index("candidates")
            candidate_id = segments[cand_idx + 1] if cand_idx + 1 < len(segments) else None
            if candidate_id:
                return candidate_id, None

        raise ValueError(
            f"Não foi possível extrair o ID do candidato a partir da URL do Workable: '{url_or_id}'. "
            "Formato esperado: https://[subdominio].workable.com/backend/jobs/[vaga]/candidates/[id] ou ID direto."
        )

    # Direct candidate ID (e.g. c12345, 12345, c-4890)
    cleaned_id = raw.strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", cleaned_id):
        raise ValueError(f"Identificador de candidato Workable inválido: '{cleaned_id}'")

    return cleaned_id, None


def format_candidate_cv(candidate: dict[str, Any]) -> str:
    """Format Workable candidate profile into a structured CV document for Transcriptor context."""
    sections: list[str] = []

    name = candidate.get("name") or f"{candidate.get('firstname', '')} {candidate.get('lastname', '')}".strip()
    headline = candidate.get("headline") or ""
    summary = strip_html(candidate.get("summary") or "")

    header_parts = [f"# {name}"] if name else ["# Candidato"]
    if headline:
        header_parts.append(f"**{headline}**")
    sections.append("\n".join(header_parts))

    # Basic metadata
    meta_lines: list[str] = []
    if candidate.get("email"):
        meta_lines.append(f"- **Email**: {candidate['email']}")
    if candidate.get("phone"):
        meta_lines.append(f"- **Telefone**: {candidate['phone']}")
    if candidate.get("address"):
        meta_lines.append(f"- **Localidade**: {candidate['address']}")
    socials = candidate.get("social_profiles") or []
    for sp in socials:
        s_name = sp.get("type", "Rede").title()
        s_url = sp.get("url")
        if s_url:
            meta_lines.append(f"- **{s_name}**: {s_url}")
    if meta_lines:
        sections.append("## Informações de Contato\n" + "\n".join(meta_lines))

    # Summary
    if summary:
        sections.append(f"## Resumo Profissional\n{summary}")

    # Experience
    experiences = candidate.get("experience_entries") or []
    if experiences:
        exp_lines: list[str] = ["## Experiência Profissional"]
        for exp in experiences:
            title = exp.get("title", "Cargo não especificado")
            company = exp.get("company", "Empresa não informada")
            start = exp.get("start_date") or ""
            end = "Atual" if exp.get("current") else (exp.get("end_date") or "")
            dates = f"{start} – {end}".strip(" –")
            exp_header = f"### {title} @ {company}"
            if dates:
                exp_header += f" ({dates})"
            exp_lines.append(exp_header)
            if exp.get("industry"):
                exp_lines.append(f"*Setor*: {exp['industry']}")
            exp_summary = strip_html(exp.get("summary") or "")
            if exp_summary:
                exp_lines.append(exp_summary)
            exp_lines.append("")
        sections.append("\n".join(exp_lines).rstrip())

    # Education
    education = candidate.get("education_entries") or []
    if education:
        edu_lines: list[str] = ["## Formação Acadêmica"]
        for edu in education:
            degree = edu.get("degree") or ""
            field = edu.get("field_of_study") or ""
            school = edu.get("school") or "Instituição de Ensino"
            start = edu.get("start_date") or ""
            end = edu.get("end_date") or ""
            dates = f"{start} – {end}".strip(" –")
            if degree and field:
                degree_str = f"{degree} em {field}"
            elif degree:
                degree_str = degree
            elif field:
                degree_str = field
            else:
                degree_str = "Graduação"
            edu_entry = f"- **{degree_str}** — {school}"
            if dates:
                edu_entry += f" ({dates})"
            edu_lines.append(edu_entry)
        sections.append("\n".join(edu_lines))

    # Skills
    skills = candidate.get("skills") or []
    if skills:
        skill_names = [s.get("name") if isinstance(s, dict) else str(s) for s in skills]
        sections.append("## Competências e Conhecimentos\n" + ", ".join(skill_names))

    # Application cover letter or custom answers
    cover_letter = strip_html(candidate.get("cover_letter") or "")
    if cover_letter:
        sections.append(f"## Carta de Apresentação / Mensagem do Candidato\n{cover_letter}")

    answers = candidate.get("answers") or []
    if answers:
        ans_lines: list[str] = ["## Respostas do Formulário de Candidatura"]
        for ans in answers:
            q = ans.get("question", {}).get("body") if isinstance(ans.get("question"), dict) else ans.get("question")
            body = ans.get("body") or ans.get("checked_options") or ans.get("file_url")
            if q and body:
                ans_lines.append(f"- **Pergunta**: {strip_html(str(q))}\n  **Resposta**: {strip_html(str(body))}")
        if len(ans_lines) > 1:
            sections.append("\n".join(ans_lines))

    return "\n\n".join(sections).strip()


def format_job_description(job: dict[str, Any]) -> str:
    """Format Workable job details into a structured Job Description document for Transcriptor."""
    sections: list[str] = []

    title = job.get("title") or job.get("full_title") or "Posição Executiva"
    sections.append(f"# {title}")

    # Job metadata
    meta_lines: list[str] = []
    if job.get("department"):
        meta_lines.append(f"- **Departamento**: {job['department']}")
    if job.get("code"):
        meta_lines.append(f"- **Código da Vaga**: {job['code']}")
    loc = job.get("location") or {}
    city = loc.get("city")
    region = loc.get("region")
    country = loc.get("country")
    loc_parts = [p for p in [city, region, country] if p]
    if loc_parts:
        meta_lines.append(f"- **Local**: {', '.join(loc_parts)}")
    wp_type = job.get("workplace_type") or loc.get("workplace_type")
    if wp_type:
        meta_lines.append(f"- **Modelo**: {wp_type.capitalize()}")
    if job.get("employment_type"):
        meta_lines.append(f"- **Tipo de Contratação**: {job['employment_type']}")
    if meta_lines:
        sections.append("## Detalhes da Vaga\n" + "\n".join(meta_lines))

    # Description
    desc = strip_html(job.get("description") or "")
    if desc:
        sections.append(f"## Descrição da Posição e Desafios\n{desc}")

    # Requirements
    reqs = strip_html(job.get("requirements") or "")
    if reqs:
        sections.append(f"## Requisitos e Qualificações Esperadas\n{reqs}")

    # Benefits
    benefits = strip_html(job.get("benefits") or "")
    if benefits:
        sections.append(f"## Benefícios e Proposta de Valor\n{benefits}")

    # Fallback to full_description if specific sections were empty
    if len(sections) <= 2:
        full_desc = strip_html(job.get("full_description") or "")
        if full_desc and full_desc not in desc:
            sections.append(f"## Detalhes Completos da Vaga\n{full_desc}")

    return "\n\n".join(sections).strip()


def format_candidate_briefing(
    candidate: dict[str, Any],
    activities: list[dict[str, Any]],
    job: dict[str, Any] | None = None,
) -> str:
    """Format pre-interview briefing incorporating stage history, past interviewer notes, and evaluations."""
    sections: list[str] = []

    name = candidate.get("name") or "Candidato"
    sections.append(f"# Briefing Pré-Entrevista: {name}")

    # Current status summary
    status_lines: list[str] = []
    if candidate.get("stage"):
        status_lines.append(f"- **Etapa Atual no Workable**: {candidate['stage']}")
    if job and job.get("title"):
        status_lines.append(f"- **Vaga Vinculada**: {job['title']}")
    status_lines.append(f"- **Origem**: {'Sourced (hunting ativo)' if candidate.get('sourced') else 'Inscrição Direta'}")
    if candidate.get("disqualified"):
        reason = candidate.get("disqualification_reason") or "Não especificado"
        status_lines.append(f"- **Status**: Desqualificado ({reason})")
    tags = candidate.get("tags") or []
    if tags:
        status_lines.append(f"- **Tags**: {', '.join(tags)}")
    sections.append("## Status do Processo Seletivo\n" + "\n".join(status_lines))

    # Notes, evaluations, and comments from activities
    relevant_activities = [
        act for act in activities
        if act.get("action") in ("comment", "rating", "evaluation", "message", "disqualification")
    ]

    if relevant_activities:
        act_lines: list[str] = ["## Histórico de Notas e Avaliações de Outros Entrevistadores (Workable)"]
        for act in relevant_activities:
            act_type = act.get("action", "nota")
            created = (act.get("created_at") or "")[:10]
            member = act.get("member") or {}
            member_name = member.get("name") or "Avaliador"
            stage = act.get("stage_name") or act.get("action_stage", {}).get("name") or ""
            stage_suffix = f" (Etapa: {stage})" if stage else ""

            body = strip_html(act.get("body") or "")
            rating = act.get("rating")
            rating_text = ""
            if rating and isinstance(rating, dict):
                score = rating.get("score") or rating.get("value")
                scale = rating.get("scale") or ""
                if score is not None:
                    rating_text = f" [Avaliação: {score}/{scale or '5'}]" if scale else f" [Avaliação: {score}]"

            header = f"### {created} — {member_name}{stage_suffix}{rating_text}"
            act_lines.append(header)
            if body:
                act_lines.append(body)
            act_lines.append("")
        sections.append("\n".join(act_lines).rstrip())
    else:
        sections.append(
            "## Histórico de Notas e Avaliações (Workable)\n"
            "*Nenhuma nota ou avaliação anterior registrada para este candidato na timeline do Workable.*"
        )

    sections.append(
        "## Instruções para a Entrevista Executiva (Ella Executive Search)\n"
        "1. Valide a coerência das transições de carreira e resultados mensuráveis descritos no histórico.\n"
        "2. Aprofunde os pontos de atenção ou elogios destacados pelas notas anteriores dos entrevistadores acima.\n"
        "3. Avalie o fit cultural e liderança conforme as competências requeridas na Descrição da Vaga."
    )

    return "\n\n".join(sections).strip()


class WorkableClient:
    """Async HTTP client for Workable SPI v3."""

    def __init__(
        self,
        subdomain: str | None = None,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.subdomain = subdomain.strip().lower() if subdomain else None
        self.api_key = api_key.strip() if api_key else None
        self._external_client = client
        self._timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.subdomain and self.api_key)

    @property
    def base_url(self) -> str:
        if not self.subdomain:
            raise WorkableConfigurationError("WORKABLE_SUBDOMAIN não está configurado.")
        return WORKABLE_SPI_BASE_TEMPLATE.format(subdomain=self.subdomain)

    def _get_headers(self) -> dict[str, str]:
        if not self.api_key:
            raise WorkableConfigurationError("WORKABLE_API_KEY não está configurado.")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "Transcriptor-Executive-Search/1.0",
        }

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        headers = self._get_headers()

        async def _do_req(c: httpx.AsyncClient) -> httpx.Response:
            return await c.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_data,
                timeout=self._timeout,
            )

        try:
            if self._external_client is not None:
                response = await _do_req(self._external_client)
            else:
                async with httpx.AsyncClient() as client:
                    response = await _do_req(client)
        except httpx.TimeoutException as exc:
            logger.warning("Workable API timeout on %s: %s", path, exc)
            raise WorkableError(f"Tempo limite esgotado ao contatar Workable ({path})") from exc
        except httpx.RequestError as exc:
            logger.warning("Workable API network error on %s: %s", path, exc)
            raise WorkableError(f"Erro de conexão com Workable: {exc}") from exc

        if response.status_code == 404:
            raise WorkableNotFoundError(f"Recurso não encontrado no Workable: {path}")
        if response.status_code in (401, 403):
            raise WorkableAuthError("Falha de autenticação no Workable. Verifique WORKABLE_API_KEY.")
        if response.status_code == 429:
            raise WorkableRateLimitError("Limite de requisições do Workable atingido (HTTP 429).")
        if response.status_code >= 400:
            logger.warning("Workable error %d on %s: %s", response.status_code, path, response.text[:200])
            raise WorkableError(
                f"Erro na API do Workable (HTTP {response.status_code}): {response.text[:100]}"
            )

        if not response.content:
            return {}
        return response.json()

    async def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        """Fetch full candidate profile: GET /spi/v3/candidates/{id}."""
        res = await self._request("GET", f"/candidates/{candidate_id}")
        # Workable response may wrap candidate in {"candidate": {...}} or return directly
        if isinstance(res, dict) and "candidate" in res and isinstance(res["candidate"], dict):
            return res["candidate"]
        return res

    async def get_job(self, shortcode: str) -> dict[str, Any]:
        """Fetch full job details: GET /spi/v3/jobs/{shortcode}."""
        res = await self._request("GET", f"/jobs/{shortcode}")
        # Workable response may wrap in {"job": {...}} or return directly
        if isinstance(res, dict) and "job" in res and isinstance(res["job"], dict):
            return res["job"]
        return res

    async def get_candidate_activities(
        self,
        candidate_id: str,
        actions: str = "comment,rating,evaluation",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch candidate activity stream (comments, notes, ratings): GET /spi/v3/candidates/{id}/activities."""
        params: dict[str, Any] = {"limit": limit}
        if actions:
            params["actions"] = actions
        res = await self._request("GET", f"/candidates/{candidate_id}/activities", params=params)
        if isinstance(res, dict) and "activities" in res and isinstance(res["activities"], list):
            return res["activities"]
        if isinstance(res, list):
            return res
        return []

    async def post_candidate_comment(
        self,
        candidate_id: str,
        body: str,
        policy: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add a comment/feedback to candidate timeline: POST /spi/v3/candidates/{id}/comments."""
        comment_payload: dict[str, Any] = {"body": body}
        if policy:
            comment_payload["policy"] = policy
        payload = {"comment": comment_payload}
        res = await self._request("POST", f"/candidates/{candidate_id}/comments", json_data=payload)
        return res

    async def get_events(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
    ) -> list[WorkableEvent]:
        """Fetch scheduled recruiting events: GET /spi/v3/events."""
        params: dict[str, Any] = {"limit": limit}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        res = await self._request("GET", "/events", params=params)
        raw_events = res.get("events", []) if isinstance(res, dict) else []
        parsed_events: list[WorkableEvent] = []
        for e in raw_events:
            if not isinstance(e, dict):
                continue
            cand = e.get("candidate") or {}
            job = e.get("job") or {}
            conf = e.get("conference") or {}
            members = e.get("members") or []
            parsed_events.append(
                WorkableEvent(
                    id=str(e.get("id")),
                    title=e.get("title") or "Entrevista",
                    event_type=str(e.get("type", "interview")),
                    starts_at=str(e.get("starts_at")),
                    ends_at=str(e.get("ends_at")) if e.get("ends_at") else None,
                    cancelled=bool(e.get("cancelled", False)),
                    candidate_id=str(cand.get("id")) if cand.get("id") else None,
                    candidate_name=cand.get("name"),
                    job_shortcode=str(job.get("shortcode")) if job.get("shortcode") else None,
                    job_title=job.get("title"),
                    conference_url=conf.get("url"),
                    interviewers=[m.get("name") for m in members if isinstance(m, dict) and m.get("name")],
                )
            )
        return parsed_events

    async def get_jobs(self, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch jobs: GET /spi/v3/jobs."""
        params: dict[str, Any] = {"limit": limit}
        if state:
            params["state"] = state
        res = await self._request("GET", "/jobs", params=params)
        return res.get("jobs", []) if isinstance(res, dict) else []

    async def get_candidates_for_job(self, shortcode: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch candidates for a job: GET /spi/v3/jobs/{shortcode}/candidates."""
        res = await self._request("GET", f"/jobs/{shortcode}/candidates", params={"limit": limit})
        return res.get("candidates", []) if isinstance(res, dict) else []

    async def import_candidate_dossier(self, url_or_id: str) -> WorkableCandidateDossier:
        """Fetch candidate, associated job, and previous notes to assemble a complete dossier."""
        candidate_id, url_shortcode = parse_workable_candidate_input(url_or_id)

        # 1. Fetch candidate
        candidate_data = await self.get_candidate(candidate_id)
        if not candidate_data or not isinstance(candidate_data, dict):
            raise WorkableNotFoundError(f"Candidato {candidate_id} não encontrado no Workable.")

        name = (
            candidate_data.get("name")
            or f"{candidate_data.get('firstname', '')} {candidate_data.get('lastname', '')}".strip()
            or f"Candidato {candidate_id}"
        )

        # Determine job shortcode
        job_meta = candidate_data.get("job") or {}
        job_shortcode = (
            job_meta.get("shortcode")
            or job_meta.get("id")
            or url_shortcode
        )

        # 2. Fetch job if shortcode available
        job_data: dict[str, Any] | None = None
        job_title = job_meta.get("title")
        if job_shortcode:
            try:
                job_data = await self.get_job(str(job_shortcode))
                if job_data and not job_title:
                    job_title = job_data.get("title") or job_data.get("full_title")
            except Exception as exc:
                logger.warning("Could not fetch Workable job shortcode %s: %s", job_shortcode, exc)

        # 3. Fetch candidate activities (comments, ratings, notes)
        activities: list[dict[str, Any]] = []
        try:
            activities = await self.get_candidate_activities(candidate_id)
        except Exception as exc:
            logger.warning("Could not fetch activities for candidate %s: %s", candidate_id, exc)

        # 4. Synthesize context documents
        cv_text = format_candidate_cv(candidate_data)
        jd_text = format_job_description(job_data) if job_data else (f"# Vaga: {job_title}" if job_title else "")
        briefing_text = format_candidate_briefing(candidate_data, activities, job_data)

        return WorkableCandidateDossier(
            candidate_id=candidate_id,
            candidate_name=name,
            job_shortcode=str(job_shortcode) if job_shortcode else None,
            job_title=job_title,
            candidate_email=candidate_data.get("email"),
            current_stage=candidate_data.get("stage"),
            jd_text=jd_text,
            cv_text=cv_text,
            briefing_text=briefing_text,
            raw_candidate=candidate_data,
        )
