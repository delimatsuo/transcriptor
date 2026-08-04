"""Hardware-free tests for the pre-interview audio preflight."""

from __future__ import annotations

import numpy as np

from backend.scripts import preflight_audio


class _FakeInputStream:
    def __init__(self, *, device: int, **_: object) -> None:
        self.device = device

    def __enter__(self) -> "_FakeInputStream":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _frames: int) -> tuple[np.ndarray, None]:
        return np.array([[0.25]], dtype=np.float32), None


def test_blank_microphone_uses_and_identifies_actual_default_device(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(preflight_audio, "SECONDS", 0.1)
    monkeypatch.setattr(preflight_audio, "get_default_input_device", lambda: 7)
    monkeypatch.setattr(
        preflight_audio,
        "find_input_device",
        lambda _name: (_ for _ in ()).throw(AssertionError("named lookup used")),
    )
    monkeypatch.setattr(preflight_audio.sd, "InputStream", _FakeInputStream)
    monkeypatch.setattr(
        preflight_audio.sd,
        "query_devices",
        lambda index: {"name": "Headset Microphone"} if index == 7 else None,
    )

    assert preflight_audio.rms_meter("", "microphone", 10) is True

    output = capsys.readouterr().out
    assert "device=[7] Headset Microphone" in output


def test_named_device_uses_named_lookup_and_identifies_actual_device(
    monkeypatch, capsys
) -> None:
    requested_names: list[str] = []
    monkeypatch.setattr(preflight_audio, "SECONDS", 0.1)
    monkeypatch.setattr(
        preflight_audio,
        "get_default_input_device",
        lambda: (_ for _ in ()).throw(AssertionError("default lookup used")),
    )
    monkeypatch.setattr(
        preflight_audio,
        "find_input_device",
        lambda name: requested_names.append(name) or 11,
    )
    monkeypatch.setattr(preflight_audio.sd, "InputStream", _FakeInputStream)
    monkeypatch.setattr(
        preflight_audio.sd,
        "query_devices",
        lambda index: {"name": "BlackHole 2ch"} if index == 11 else None,
    )

    assert preflight_audio.rms_meter("BlackHole", "system-audio", 10) is True

    assert requested_names == ["BlackHole"]
    output = capsys.readouterr().out
    assert "device=[11] BlackHole 2ch" in output
