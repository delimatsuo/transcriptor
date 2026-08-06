"""Suggestion spend is driven by candidate responses, not interviewer speech."""

from types import SimpleNamespace

from backend import main
from backend.config import Settings


def test_candidate_trigger_uses_effective_source_label(monkeypatch):
    monkeypatch.setattr(
        main,
        "settings",
        Settings(
            google_cloud_project="test-project",
            stt_speaker_label_other="Candidato",
        ),
    )

    candidate = SimpleNamespace(
        is_final=True,
        speaker="Entrevistador",
        speaker_override="Candidato",
    )
    interviewer = SimpleNamespace(
        is_final=True,
        speaker="Entrevistador",
        speaker_override=None,
    )
    interim = SimpleNamespace(
        is_final=False,
        speaker="Candidato",
        speaker_override=None,
    )

    assert main._is_candidate_final_segment(candidate) is True
    assert main._is_candidate_final_segment(interviewer) is False
    assert main._is_candidate_final_segment(interim) is False
