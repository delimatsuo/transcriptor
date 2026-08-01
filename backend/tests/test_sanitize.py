"""Tests for sanitize_participant_name — the only validation on untrusted
Chrome-extension input before it reaches transcripts, Firestore, and the UI.
"""

from __future__ import annotations

from backend.utils.sanitize import MAX_NAME_LENGTH, sanitize_participant_name


def test_simple_name_passes_through():
    assert sanitize_participant_name("Alice Smith") == "Alice Smith"


def test_empty_string_returns_empty():
    assert sanitize_participant_name("") == ""


def test_whitespace_only_returns_empty():
    assert sanitize_participant_name("   ") == ""


def test_control_characters_are_stripped():
    assert sanitize_participant_name("Alice\x00\x01Smith") == "AliceSmith"


def test_newlines_are_stripped():
    assert sanitize_participant_name("Alice\nSmith\r\n") == "AliceSmith"


def test_over_length_name_is_truncated():
    long_name = "A" * (MAX_NAME_LENGTH + 20)
    result = sanitize_participant_name(long_name)
    assert len(result) == MAX_NAME_LENGTH
    assert result == "A" * MAX_NAME_LENGTH


def test_disallowed_characters_reject_the_whole_name():
    # Google Meet display names are attacker-influenced input; anything that
    # isn't a plain name character must be rejected outright, not stripped.
    assert sanitize_participant_name("<script>alert(1)</script>") == ""
    assert sanitize_participant_name("Alice@Smith") == ""
    assert sanitize_participant_name("Alice/Smith") == ""


def test_hyphen_apostrophe_and_period_are_allowed():
    assert sanitize_participant_name("Mary-Jane O'Brien") == "Mary-Jane O'Brien"
    assert sanitize_participant_name("Dr. Smith") == "Dr. Smith"


def test_portuguese_accented_names_are_allowed():
    # BR-PT is this product's primary language — accented names must survive.
    assert sanitize_participant_name("João da Conceição") == "João da Conceição"
    assert sanitize_participant_name("Luís Gonçalves") == "Luís Gonçalves"
