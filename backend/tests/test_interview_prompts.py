"""Suggestion prompt bounds prevent repeated oversized provider inputs."""

from backend.llm.interview_prompts import (
    MAX_SUGGESTION_BRIEFING_CHARS,
    MAX_SUGGESTION_CANDIDATE_NAME_CHARS,
    MAX_SUGGESTION_JD_CHARS,
    MAX_SUGGESTION_RESUME_CHARS,
    MAX_SUGGESTION_TRANSCRIPT_CHARS,
    _bound_suggestion_text,
    build_interview_user_message,
)


def test_bound_keeps_head_and_tail_and_marks_truncation():
    value = "head-" + ("x" * 100) + "-tail"
    bounded = _bound_suggestion_text(value, 100)

    assert len(bounded) == 100
    assert bounded.startswith("head-")
    assert bounded.endswith("-tail")
    assert "truncado" in bounded


def test_interview_suggestion_message_bounds_each_repeated_context_field():
    message = build_interview_user_message(
        resume_text="R" * (MAX_SUGGESTION_RESUME_CHARS + 100),
        jd_text="J" * (MAX_SUGGESTION_JD_CHARS + 100),
        briefing_text="B" * (MAX_SUGGESTION_BRIEFING_CHARS + 100),
        recent_transcript="T" * (MAX_SUGGESTION_TRANSCRIPT_CHARS + 100),
        candidate_name="N" * (MAX_SUGGESTION_CANDIDATE_NAME_CHARS + 100),
    )

    assert message.count("conteúdo truncado") == 5
    assert len(message.split("## Currículo / CV do Candidato\n", 1)[1].split("\n\n## ", 1)[0]) == MAX_SUGGESTION_RESUME_CHARS
    assert len(message.split("## Descrição da Vaga / Job Description\n", 1)[1].split("\n\n## ", 1)[0]) == MAX_SUGGESTION_JD_CHARS
    assert len(message.split("## Briefing Pré-Entrevista\n", 1)[1].split("\n\n## ", 1)[0]) == MAX_SUGGESTION_BRIEFING_CHARS
    assert len(message.split("## Últimas Trocas da Entrevista (transcrição ao vivo)\n", 1)[1]) == MAX_SUGGESTION_TRANSCRIPT_CHARS
