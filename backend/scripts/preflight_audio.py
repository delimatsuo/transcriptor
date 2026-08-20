"""Pre-interview audio routing check: are BOTH capture channels alive?

Usage: wear a headset, follow docs/launch/preflight-checklist.md, then run:
  .venv/bin/python3 -m backend.scripts.preflight_audio
PASS here requires signal on BOTH devices within 10 seconds. The checklist's
source-isolation gate is also mandatory before a real interview.
"""

import numpy as np
import sounddevice as sd

from backend.audio.capture import find_input_device, get_default_input_device
from backend.config import get_settings

THRESHOLD = 0.001  # RMS floor ≈ silence
SECONDS = 10


def rms_meter(
    device_name: str,
    label: str,
    samplerate: int,
    input_channel: int = 0,
) -> bool:
    """Report whether an input device produces an audible signal."""
    idx = find_input_device(device_name) if device_name else get_default_input_device()
    device = sd.query_devices(idx)
    actual_device_name = str(device["name"])
    peak = 0.0
    with sd.InputStream(
        device=idx,
        channels=max(1, input_channel + 1),
        samplerate=samplerate,
        dtype="float32",
    ) as stream:
        for _ in range(int(SECONDS * 10)):
            data, _ = stream.read(samplerate // 10)
            selected = data[:, input_channel] if data.ndim > 1 else data
            peak = max(peak, float(np.sqrt(np.mean(np.square(selected)))))
    ok = peak > THRESHOLD
    print(
        f"{'PASS' if ok else 'FAIL'}  {label:<14} peak RMS={peak:.5f}  "
        f"(device=[{idx}] {actual_device_name})"
    )
    return ok


def main() -> None:
    """Measure microphone and system-audio input channels."""
    settings = get_settings()
    mic_ok = rms_meter(
        settings.microphone_device_name,
        "microphone",
        settings.sample_rate,
        settings.microphone_input_channel,
    )
    sys_ok = rms_meter(settings.blackhole_device_name, "system-audio", settings.sample_rate)
    if not sys_ok:
        print(
            "\nsystem-audio FAIL → check: macOS output device must be the "
            "Multi-Output Device (containing BlackHole 2ch + your headphones). "
            "Windows: meeting app output → CABLE Input, 'Listen to this device' on."
        )
    raise SystemExit(0 if (mic_ok and sys_ok) else 1)


if __name__ == "__main__":
    main()
