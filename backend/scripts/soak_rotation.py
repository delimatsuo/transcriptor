"""Manual soak: run live capture >2 rotations; verify no transcript gap.

Usage: .venv/bin/python3 -m backend.scripts.soak_rotation
Speak continuously (read aloud / play a podcast into the mic) for ~10 min.
The script prints every final segment with its offset; afterwards it reports
the largest inter-segment gap that overlaps a rotation boundary (~270s, ~540s).
A gap > 5s at a boundary while audio was playing = rotation still loses audio.
"""

import asyncio
import time

from backend.audio.buffer import AudioBuffer
from backend.audio.capture import AudioCapture
from backend.config import get_settings
from backend.schemas.models import TranscriptSegment
from backend.stt.stream_manager import StreamManager

ROTATION = 270.0


async def main() -> None:
    settings = get_settings()
    finals: list[tuple[float, str]] = []
    t0 = time.monotonic()

    def on_transcript(seg: TranscriptSegment) -> None:
        if seg.is_final:
            offset = time.monotonic() - t0
            finals.append((offset, seg.text))
            print(f"[{offset:7.1f}s] {seg.text}")

    queue: asyncio.Queue = asyncio.Queue()
    capture = AudioCapture(
        settings,
        queue,
        device_name=settings.microphone_device_name,
        label="soak",
    )
    buffer = AudioBuffer(settings, queue)
    mgr = StreamManager(settings, on_transcript=on_transcript, source_label="soak")

    await capture.start()
    await buffer.start()
    await mgr.start()
    try:
        async for chunk in buffer.chunks():
            await mgr.send_audio(buffer.float32_to_int16(chunk))
            if time.monotonic() - t0 > 600:
                break
    finally:
        await mgr.stop()
        await buffer.stop()
        await capture.stop()

    print("\n--- gaps at rotation boundaries ---")
    for i in range(1, len(finals)):
        gap = finals[i][0] - finals[i - 1][0]
        for k in (1, 2):
            if finals[i - 1][0] < k * ROTATION < finals[i][0] and gap > 5:
                print(f"SUSPECT GAP {gap:.1f}s across {k * ROTATION:.0f}s boundary")
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
