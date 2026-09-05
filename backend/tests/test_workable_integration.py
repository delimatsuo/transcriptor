"""Tests for Workable ATS direct integration and MCP adapter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.integrations.workable import (
    WorkableAuthError,
    WorkableCandidateDossier,
    WorkableClient,
    WorkableConfigurationError,
    WorkableError,
    WorkableNotFoundError,
    WorkableRateLimitError,
    format_candidate_briefing,
    format_candidate_cv,
    format_job_description,
    parse_workable_candidate_input,
    strip_html,
)
from backend.mcp import workable_mcp
from backend.schemas.models import SessionMode, SessionStatus


# --- Fixtures ---

MOCK_CANDIDATE_DATA: dict[str, Any] = {
    "id": "c12345",
    "name": "Carlos Eduardo Mendonça",
    "firstname": "Carlos Eduardo",
    "lastname": "Mendonça",
    "headline": "Diretor de Operações e Supply Chain | Ex-COO",
    "email": "carlos.mendonca@example.com",
    "phone": "+55 11 98765-4321",
    "address": "São Paulo, SP, Brasil",
    "stage": "Entrevista Executiva",
    "sourced": True,
    "disqualified": False,
    "summary": "<p>Profissional com mais de 15 anos liderando <b>operações multinacionais</b> e transformação digital.</p>",
    "experience_entries": [
        {
            "title": "Chief Operating Officer",
            "company": "LogTech Brasil",
            "start_date": "2021-01",
            "current": True,
            "industry": "Logística e Tecnologia",
            "summary": "<ul><li>Reestruturação de malha logística com ganho de 25% em produtividade</li><li>Gestão direta de 350 colaboradores</li></ul>",
        },
        {
            "title": "Gerente Geral de Operações",
            "company": "Varejo Global S/A",
            "start_date": "2016-03",
            "end_date": "2020-12",
            "current": False,
            "industry": "Varejo",
            "summary": "Responsável por centros de distribuição e supply chain nacional.",
        },
    ],
    "education_entries": [
        {
            "degree": "MBA Executivo",
            "field_of_study": "Gestão Empresarial",
            "school": "Fundação Dom Cabral",
            "start_date": "2018",
            "end_date": "2019",
        },
        {
            "degree": "Bacharelado",
            "field_of_study": "Engenharia de Produção",
            "school": "Universidade de São Paulo (USP)",
            "start_date": "2005",
            "end_date": "2010",
        },
    ],
    "skills": ["Supply Chain", "Gestão de P&L", "Transformação Digital", "Liderança de Equipes"],
    "social_profiles": [
        {"type": "linkedin", "url": "https://linkedin.com/in/carlos-mendonca-ops"}
    ],
    "job": {
        "id": "job_9988",
        "shortcode": "OPS2026",
        "title": "Diretor de Operações (COO)",
    },
    "tags": ["prioritário", "c-level"],
}

MOCK_JOB_DATA: dict[str, Any] = {
    "id": "job_9988",
    "shortcode": "OPS2026",
    "title": "Diretor de Operações (COO)",
    "code": "COO-2026-SP",
    "department": "Operações & Supply Chain",
    "workplace_type": "hybrid",
    "employment_type": "Full-time (CLT Executivo)",
    "location": {
        "city": "São Paulo",
        "region": "SP",
        "country": "Brasil",
    },
    "description": (
        "<p>Buscamos um <strong>Diretor de Operações</strong> para liderar nossa expansão nacional.</p>"
        "<p>O profissional será responsável pela integração operacional e gestão do P&L industrial.</p>"
    ),
    "requirements": (
        "<ul>"
        "<li>Experiência sólida como Diretor ou VP de Operações</li>"
        "<li>Forte conhecimento em supply chain complexa e metodologias ágeis</li>"
        "<li>Inglês fluente para reporte internacional</li>"
        "</ul>"
    ),
    "benefits": "<p>Remuneração agressiva com bônus anual e plano de stock options.</p>",
}

MOCK_ACTIVITIES_DATA: list[dict[str, Any]] = [
    {
        "action": "comment",
        "created_at": "2026-08-15T14:30:00Z",
        "member": {"name": "Mariana Souza", "id": "m1"},
        "stage_name": "Triagem e Hunting",
        "body": "<p>Candidato abordado via hunting. Demonstrou grande clareza e excelente energia na conversa inicial.</p>",
    },
    {
        "action": "rating",
        "created_at": "2026-08-18T10:00:00Z",
        "member": {"name": "Rodrigo Silva", "id": "m2"},
        "stage_name": "Entrevista RH",
        "rating": {"score": 5, "scale": "5"},
        "body": "<p>Aprovadíssimo. Experiência de escala comprovada e ótimo alinhamento cultural com o cliente.</p>",
    },
]


# --- Unit Tests: HTML Stripper ---

def test_strip_html_handles_tags_and_formatting():
    raw = (
        "<h3>Título</h3>\n"
        "<p>Primeiro parágrafo com <strong>negrito</strong> e <em>itálico</em>.</p>\n"
        "<ul><li>Item 1</li><li>Item 2</li></ul>\n"
        "<p>Linha com quebra<br/>e entidade &amp; &lt;teste&gt; &quot;aspas&quot;.</p>"
    )
    cleaned = strip_html(raw)
    assert "### Título" in cleaned
    assert "**negrito**" in cleaned
    assert "_itálico_" in cleaned
    assert "• Item 1" in cleaned
    assert "• Item 2" in cleaned
    assert "& <teste> \"aspas\"" in cleaned


def test_strip_html_handles_empty_or_none():
    assert strip_html("") == ""
    assert strip_html(None) == ""


# --- Unit Tests: URL and ID Parser ---

def test_parse_workable_candidate_input_variants():
    # Full candidate + job URL
    cid, shortcode = parse_workable_candidate_input(
        "https://acme.workable.com/backend/jobs/OPS2026/candidates/c12345"
    )
    assert cid == "c12345"
    assert shortcode == "OPS2026"

    # URL with query string
    cid, shortcode = parse_workable_candidate_input(
        "https://acme.workable.com/backend/jobs/OPS2026/candidates/c12345?tab=timeline"
    )
    assert cid == "c12345"
    assert shortcode == "OPS2026"

    # URL without scheme
    cid, shortcode = parse_workable_candidate_input(
        "acme.workable.com/backend/jobs/OPS2026/candidates/c12345"
    )
    assert cid == "c12345"
    assert shortcode == "OPS2026"

    # Direct candidate URL without job
    cid, shortcode = parse_workable_candidate_input(
        "https://acme.workable.com/candidates/c12345"
    )
    assert cid == "c12345"
    assert shortcode is None

    # App domain candidate URL
    cid, shortcode = parse_workable_candidate_input(
        "https://app.workable.com/candidates/c12345"
    )
    assert cid == "c12345"
    assert shortcode is None

    # Raw candidate ID
    cid, shortcode = parse_workable_candidate_input("c12345")
    assert cid == "c12345"
    assert shortcode is None

    cid, shortcode = parse_workable_candidate_input("998877")
    assert cid == "998877"
    assert shortcode is None


def test_parse_workable_candidate_input_invalid():
    with pytest.raises(ValueError, match="não pode estar em branco"):
        parse_workable_candidate_input("   ")

    with pytest.raises(ValueError, match="Identificador de candidato Workable inválido"):
        parse_workable_candidate_input("cand id com espacos!")

    with pytest.raises(ValueError, match="Não foi possível extrair o ID do candidato"):
        parse_workable_candidate_input("https://acme.workable.com/about/company")


# --- Unit Tests: Context Formatters ---

def test_format_candidate_cv():
    cv_text = format_candidate_cv(MOCK_CANDIDATE_DATA)
    assert "# Carlos Eduardo Mendonça" in cv_text
    assert "Diretor de Operações e Supply Chain" in cv_text
    assert "carlos.mendonca@example.com" in cv_text
    assert "## Resumo Profissional" in cv_text
    assert "## Experiência Profissional" in cv_text
    assert "Chief Operating Officer @ LogTech Brasil (2021-01 – Atual)" in cv_text
    assert "## Formação Acadêmica" in cv_text
    assert "**MBA Executivo em Gestão Empresarial** — Fundação Dom Cabral" in cv_text
    assert "## Competências e Conhecimentos" in cv_text
    assert "Supply Chain, Gestão de P&L" in cv_text


def test_format_job_description():
    jd_text = format_job_description(MOCK_JOB_DATA)
    assert "# Diretor de Operações (COO)" in jd_text
    assert "- **Departamento**: Operações & Supply Chain" in jd_text
    assert "- **Local**: São Paulo, SP, Brasil" in jd_text
    assert "## Descrição da Posição e Desafios" in jd_text
    assert "liderar nossa expansão nacional" in jd_text
    assert "## Requisitos e Qualificações Esperadas" in jd_text
    assert "• Experiência sólida como Diretor ou VP de Operações" in jd_text
    assert "## Benefícios e Proposta de Valor" in jd_text


def test_format_candidate_briefing():
    briefing_text = format_candidate_briefing(
        MOCK_CANDIDATE_DATA,
        MOCK_ACTIVITIES_DATA,
        MOCK_JOB_DATA,
    )
    assert "# Briefing Pré-Entrevista: Carlos Eduardo Mendonça" in briefing_text
    assert "- **Etapa Atual no Workable**: Entrevista Executiva" in briefing_text
    assert "- **Vaga Vinculada**: Diretor de Operações (COO)" in briefing_text
    assert "- **Origem**: Sourced (hunting ativo)" in briefing_text
    assert "## Histórico de Notas e Avaliações de Outros Entrevistadores (Workable)" in briefing_text
    assert "Mariana Souza (Etapa: Triagem e Hunting)" in briefing_text
    assert "Candidato abordado via hunting" in briefing_text
    assert "Rodrigo Silva (Etapa: Entrevista RH) [Avaliação: 5/5]" in briefing_text
    assert "Aprovadíssimo" in briefing_text


# --- Unit Tests: WorkableClient HTTP Calls ---

@pytest.mark.anyio
async def test_workable_client_configuration_guards():
    client_unconfigured = WorkableClient()
    assert not client_unconfigured.is_configured
    with pytest.raises(WorkableConfigurationError, match="WORKABLE_SUBDOMAIN"):
        _ = client_unconfigured.base_url

    client_no_key = WorkableClient(subdomain="acme")
    with pytest.raises(WorkableConfigurationError, match="WORKABLE_API_KEY"):
        client_no_key._get_headers()


@pytest.mark.anyio
async def test_workable_client_fetch_and_dossier_import():
    # Setup mock transport for httpx
    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        assert request.headers.get("Authorization") == "Bearer secret-test-token"

        if "/candidates/c12345/activities" in url_str:
            return httpx.Response(200, json={"activities": MOCK_ACTIVITIES_DATA})
        elif "/candidates/c12345/comments" in url_str:
            return httpx.Response(201, json={"id": "comment_123"})
        elif "/candidates/c12345" in url_str:
            return httpx.Response(200, json={"candidate": MOCK_CANDIDATE_DATA})
        elif "/jobs/OPS2026" in url_str:
            return httpx.Response(200, json={"job": MOCK_JOB_DATA})
        elif "/candidates/notfound" in url_str:
            return httpx.Response(404, json={"error": "Not Found"})
        elif "/candidates/rate-limited" in url_str:
            return httpx.Response(429, json={"error": "Too Many Requests"})
        elif "/candidates/unauthorized" in url_str:
            return httpx.Response(401, json={"error": "Unauthorized"})
        return httpx.Response(400, text="Bad Request")

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        client = WorkableClient(
            subdomain="acme",
            api_key="secret-test-token",
            client=mock_http,
        )
        assert client.is_configured
        assert client.base_url == "https://acme.workable.com/spi/v3"

        # 1. Test get_candidate
        cand = await client.get_candidate("c12345")
        assert cand["id"] == "c12345"
        assert cand["name"] == "Carlos Eduardo Mendonça"

        # 2. Test get_job
        job = await client.get_job("OPS2026")
        assert job["shortcode"] == "OPS2026"
        assert job["title"] == "Diretor de Operações (COO)"

        # 3. Test get_candidate_activities
        acts = await client.get_candidate_activities("c12345")
        assert len(acts) == 2
        assert acts[0]["action"] == "comment"

        # 4. Test post_candidate_comment
        post_res = await client.post_candidate_comment("c12345", "Comentário teste", policy=["admin", "recruiter"])
        assert post_res.get("id") == "comment_123"

        # 5. Test import_candidate_dossier
        dossier = await client.import_candidate_dossier(
            "https://acme.workable.com/backend/jobs/OPS2026/candidates/c12345"
        )
        assert isinstance(dossier, WorkableCandidateDossier)
        assert dossier.candidate_id == "c12345"
        assert dossier.candidate_name == "Carlos Eduardo Mendonça"
        assert dossier.job_shortcode == "OPS2026"
        assert "Chief Operating Officer @ LogTech Brasil" in dossier.cv_text
        assert "Diretor de Operações (COO)" in dossier.jd_text
        assert "Briefing Pré-Entrevista" in dossier.briefing_text
        assert "Mariana Souza" in dossier.briefing_text

        # 6. Test Error Handling
        with pytest.raises(WorkableNotFoundError):
            await client.get_candidate("notfound")

        with pytest.raises(WorkableAuthError):
            await client.get_candidate("unauthorized")

        with pytest.raises(WorkableRateLimitError):
            await client.get_candidate("rate-limited")


# --- Unit Tests: MCP Tools Adapter ---

@pytest.mark.anyio
async def test_workable_mcp_tools_and_execution():
    assert len(workable_mcp.WORKABLE_TOOLS) == 5
    tool_names = {t["name"] for t in workable_mcp.WORKABLE_TOOLS}
    assert "workable_get_candidate" in tool_names
    assert "workable_get_job" in tool_names
    assert "workable_get_candidate_notes" in tool_names
    assert "workable_post_feedback" in tool_names
    assert "workable_import_dossier" in tool_names

    def mock_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/candidates/c12345/activities" in url_str:
            return httpx.Response(200, json={"activities": MOCK_ACTIVITIES_DATA})
        elif "/candidates/c12345/comments" in url_str:
            return httpx.Response(201, json={"status": "created"})
        elif "/candidates/c12345" in url_str:
            return httpx.Response(200, json={"candidate": MOCK_CANDIDATE_DATA})
        elif "/jobs/OPS2026" in url_str:
            return httpx.Response(200, json={"job": MOCK_JOB_DATA})
        return httpx.Response(404)

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as mock_http:
        mock_client = WorkableClient(
            subdomain="acme",
            api_key="token-test",
            client=mock_http,
        )

        with patch("backend.mcp.workable_mcp.get_default_client", return_value=mock_client):
            # 1. get_candidate
            cand_res = await workable_mcp.execute_tool(
                "workable_get_candidate",
                {"candidate_id": "c12345"},
            )
            assert cand_res["candidate_id"] == "c12345"
            assert "Carlos Eduardo Mendonça" in cand_res["formatted_cv"]

            # 2. get_job
            job_res = await workable_mcp.execute_tool(
                "workable_get_job",
                {"shortcode": "OPS2026"},
            )
            assert job_res["title"] == "Diretor de Operações (COO)"

            # 3. get_candidate_notes
            notes_res = await workable_mcp.execute_tool(
                "workable_get_candidate_notes",
                {"candidate_id": "c12345"},
            )
            assert notes_res["activities_count"] == 2
            assert "Mariana Souza" in notes_res["formatted_briefing"]

            # 4. post_feedback
            post_res = await workable_mcp.execute_tool(
                "workable_post_feedback",
                {"candidate_id": "c12345", "feedback_text": "Excelente candidato."},
            )
            assert post_res["ok"] is True

            # 5. import_dossier
            dossier_res = await workable_mcp.execute_tool(
                "workable_import_dossier",
                {"url_or_id": "c12345"},
            )
            assert dossier_res["candidate_id"] == "c12345"
            assert dossier_res["candidate_name"] == "Carlos Eduardo Mendonça"


# --- Unit Tests: Backend FastAPI Endpoints ---

@pytest.mark.anyio
async def test_parse_workable_endpoint_unconfigured():
    from fastapi import HTTPException
    from backend import main as backend_main
    from backend.schemas.models import WorkableParseRequest

    old_settings = backend_main.settings
    backend_main.settings = Settings(
        google_cloud_project="test-project",
        workable_subdomain=None,
        workable_api_key=None,
    )
    try:
        with pytest.raises(HTTPException) as exc_info:
            await backend_main.parse_workable_dossier(WorkableParseRequest(url_or_id="c12345"))
        assert exc_info.value.status_code == 400
        assert "não configurada" in exc_info.value.detail
    finally:
        backend_main.settings = old_settings


@pytest.mark.anyio
async def test_parse_workable_endpoint_success():
    from backend import main as backend_main
    from backend.schemas.models import WorkableParseRequest

    old_settings = backend_main.settings
    backend_main.settings = Settings(
        google_cloud_project="test-project",
        workable_subdomain="acme",
        workable_api_key="secret-key",
    )
    mock_dossier = WorkableCandidateDossier(
        candidate_id="c12345",
        candidate_name="Carlos Eduardo",
        job_shortcode="OPS2026",
        job_title="Diretor de Operações",
        jd_text="Descrição da vaga",
        cv_text="CV do candidato",
        briefing_text="Briefing pré-entrevista",
    )
    try:
        with patch.object(WorkableClient, "import_candidate_dossier", AsyncMock(return_value=mock_dossier)):
            res = await backend_main.parse_workable_dossier(WorkableParseRequest(url_or_id="c12345"))
            assert res["ok"] is True
            assert res["dossier"]["candidate_name"] == "Carlos Eduardo"
            assert res["dossier"]["job_shortcode"] == "OPS2026"
    finally:
        backend_main.settings = old_settings


@pytest.mark.anyio
async def test_import_workable_to_session_endpoint():
    from backend import main as backend_main
    from backend.schemas.models import WorkableParseRequest

    old_settings = backend_main.settings
    backend_main.settings = Settings(
        google_cloud_project="test-project",
        workable_subdomain="acme",
        workable_api_key="secret-key",
    )
    backend_main.session_mgr = MagicMock()
    mock_storage = MagicMock()
    mock_storage.save_interview_context = AsyncMock()
    backend_main.firestore_storage = mock_storage

    mock_dossier = WorkableCandidateDossier(
        candidate_id="c12345",
        candidate_name="Carlos Eduardo",
        job_shortcode="OPS2026",
        job_title="COO",
        jd_text="JD Text",
        cv_text="CV Text",
        briefing_text="Briefing Text",
    )

    try:
        with (
            patch.object(backend_main, "_require_active_interview", return_value=None),
            patch.object(WorkableClient, "import_candidate_dossier", AsyncMock(return_value=mock_dossier)),
        ):
            res = await backend_main.import_workable_to_session("test-sess-1", WorkableParseRequest(url_or_id="c12345"))
            assert res["ok"] is True
            # Persists 5 context slots: candidate_name, jd, resume, briefing, workable_candidate_id
            assert mock_storage.save_interview_context.call_count == 5
            assert backend_main.interview_documents["test-sess-1"]["workable_candidate_id"] == "c12345"
            assert backend_main.interview_documents["test-sess-1"]["candidate_name"] == "Carlos Eduardo"
    finally:
        backend_main.settings = old_settings


def test_workable_subdomain_rfc1123_validation():
    # Valid subdomains
    s1 = Settings(google_cloud_project="test-proj", workable_subdomain="acme")
    assert s1.workable_subdomain == "acme"
    s2 = Settings(google_cloud_project="test-proj", workable_subdomain="acme-corp")
    assert s2.workable_subdomain == "acme-corp"
    s3 = Settings(google_cloud_project="test-proj", workable_subdomain="a1-b2")
    assert s3.workable_subdomain == "a1-b2"

    # Trailing hyphen prohibited (RFC 1123)
    with pytest.raises(ValueError, match="WORKABLE_SUBDOMAIN"):
        Settings(google_cloud_project="test-proj", workable_subdomain="acme-")

    # Leading hyphen prohibited
    with pytest.raises(ValueError, match="WORKABLE_SUBDOMAIN"):
        Settings(google_cloud_project="test-proj", workable_subdomain="-acme")

    # Dots or invalid chars prohibited
    with pytest.raises(ValueError, match="WORKABLE_SUBDOMAIN"):
        Settings(google_cloud_project="test-proj", workable_subdomain="acme.com")


@pytest.mark.anyio
async def test_export_report_to_workable_endpoint():
    from fastapi import HTTPException
    from backend import main as backend_main
    from backend.schemas.models import Session, WorkableExportRequest
    from backend.sessions.reports import (
        ClientNarrative,
        EvidenceReference,
        EvidenceSource,
        InternalReportSection,
        InterviewReport,
        ReportStatus,
    )

    old_settings = backend_main.settings
    backend_main.settings = Settings(
        google_cloud_project="test-project",
        workable_subdomain="acme",
        workable_api_key="secret-key",
    )
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    mock_session = Session(
        id="test-sess-export",
        mode=SessionMode.INTERVIEW,
        status=SessionStatus.COMPLETED,
        start_time=now,
        owner_id="user1",
        org_id="ella-internal",
    )
    backend_main.session_mgr = MagicMock()
    mock_storage = MagicMock()
    backend_main.firestore_storage = mock_storage

    approved_report = InterviewReport(
        session_id="test-sess-export",
        version=1,
        status=ReportStatus.APPROVED,
        created_at=now,
        updated_at=now,
        owner_id="user1",
        org_id="ella-internal",
        approved_version=1,
        approved_at=now,
        internal_sections=[
            InternalReportSection(
                id="sec_leadership",
                title="Liderança Executiva",
                body="Candidato demonstrou sólida liderança.",
                rating=5,
                evidence=[EvidenceReference(source=EvidenceSource.TRANSCRIPT, evidence_id="e1")],
            )
        ],
        client_narrative=ClientNarrative(
            trajectory="Trajetória sólida executiva.",
            assessment="Competências comprovadas.",
            trajectory_evidence=[EvidenceReference(source=EvidenceSource.TRANSCRIPT, evidence_id="e1")],
            assessment_evidence=[EvidenceReference(source=EvidenceSource.TRANSCRIPT, evidence_id="e2")],
        ),
    )
    mock_storage.get_interview_report = AsyncMock(return_value=approved_report)
    mock_storage.get_interview_context = AsyncMock(return_value=[
        {"type": "workable_candidate_id", "text": "c12345"},
        {"type": "candidate_name", "text": "Carlos Eduardo"},
    ])

    try:
        # Case 1: Explicit candidate_id provided
        with (
            patch.object(backend_main, "_read_completed_interview", AsyncMock(return_value=mock_session)),
            patch.object(backend_main, "_assert_session_access", return_value=MagicMock()),
            patch.object(backend_main, "_assert_report_scope", return_value=None),
            patch.object(WorkableClient, "post_candidate_comment", AsyncMock(return_value={"id": "comm_99"})),
        ):
            res = await backend_main.export_report_to_workable(
                "test-sess-export",
                WorkableExportRequest(candidate_id="c12345"),
            )
            assert res["ok"] is True
            assert res["candidate_id"] == "c12345"

        # Case 2: Default UI flow (no candidate_id, empty in-memory cache, resolved from durable Firestore context)
        backend_main.interview_documents.clear()
        with (
            patch.object(backend_main, "_read_completed_interview", AsyncMock(return_value=mock_session)),
            patch.object(backend_main, "_assert_session_access", return_value=MagicMock()),
            patch.object(backend_main, "_assert_report_scope", return_value=None),
            patch.object(WorkableClient, "post_candidate_comment", AsyncMock(return_value={"id": "comm_100"})),
        ):
            res2 = await backend_main.export_report_to_workable(
                "test-sess-export",
                WorkableExportRequest(),  # candidate_id is None
            )
            assert res2["ok"] is True
            assert res2["candidate_id"] == "c12345"

        # Case 3: Missing candidate_id both in request and in Firestore -> raises HTTP 400
        mock_storage.get_interview_context = AsyncMock(return_value=[])
        backend_main.interview_documents.clear()
        with (
            patch.object(backend_main, "_read_completed_interview", AsyncMock(return_value=mock_session)),
            patch.object(backend_main, "_assert_session_access", return_value=MagicMock()),
            patch.object(backend_main, "_assert_report_scope", return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await backend_main.export_report_to_workable(
                    "test-sess-export",
                    WorkableExportRequest(),
                )
            assert exc_info.value.status_code == 400
            assert "candidate_id" in exc_info.value.detail

        # Case 4: If report not approved -> conflict HTTP 409
        draft_report = approved_report.model_copy(update={"status": ReportStatus.DRAFT})
        mock_storage.get_interview_report = AsyncMock(return_value=draft_report)
        with (
            patch.object(backend_main, "_read_completed_interview", AsyncMock(return_value=mock_session)),
            patch.object(backend_main, "_assert_session_access", return_value=MagicMock()),
            patch.object(backend_main, "_assert_report_scope", return_value=None),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await backend_main.export_report_to_workable(
                    "test-sess-export",
                    WorkableExportRequest(candidate_id="c12345"),
                )
            assert exc_info.value.status_code == 409
    finally:
        backend_main.settings = old_settings


