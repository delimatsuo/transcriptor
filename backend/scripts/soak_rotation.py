"""Manual soak: run live capture >2 rotations; verify no transcript gap.

Usage: .venv/bin/python3 -m backend.scripts.soak_rotation
Speak continuously (read aloud / play a podcast into the mic) for ~10 min.
The script prints every final segment with provider-derived audio offsets;
afterwards it reports the uncovered audio interval at each rotation boundary
(~270s, ~540s). A gap > 5s while audio was continuously playing means rotation
still loses audio. Callback arrival intervals are not audio gaps.
"""

import asyncio
import time

from backend.audio.buffer import AudioBuffer
from backend.audio.capture import AudioCapture
from backend.config import get_settings
from backend.schemas.models import TranscriptSegment
from backend.stt.stream_manager import StreamManager

ROTATION = 270.0
MAX_BOUNDARY_GAP = 5.0


def rotation_boundary_gap(
    finals: list[TranscriptSegment], boundary: float
) -> float | None:
    """Return uncovered transcript-audio seconds around one boundary."""
    intervals = sorted(
        (segment.start_time, segment.end_time)
        for segment in finals
        if segment.is_final and segment.end_time >= segment.start_time
    )
    if any(start <= boundary <= end for start, end in intervals):
        return 0.0
    before = [end for _, end in intervals if end < boundary]
    after = [start for start, _ in intervals if start > boundary]
    if not before or not after:
        return None
    return max(0.0, min(after) - max(before))


async def main() -> None:
    settings = get_settings()
    finals: list[TranscriptSegment] = []
    t0 = time.monotonic()

    def on_transcript(seg: TranscriptSegment) -> None:
        if seg.is_final:
            finals.append(seg)
            callback_offset = time.monotonic() - t0
            print(
                f"[{seg.start_time:7.1f}-{seg.end_time:7.1f}s audio; "
                f"callback={callback_offset:7.1f}s] {seg.text}"
            )

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
    drain_completed = False
    try:
        async for chunk in buffer.chunks():
            await mgr.send_audio(buffer.float32_to_int16(chunk))
            if time.monotonic() - t0 > 600:
                break
    finally:
        drain_completed = await mgr.stop()
        await buffer.stop()
        await capture.stop()

    print("\n--- gaps at rotation boundaries ---")
    failed = not drain_completed
    if not drain_completed:
        print(f"SUSPECT DRAIN FAILURE {mgr.drain_failure_reason}")
    for k in (1, 2):
        boundary = k * ROTATION
        gap = rotation_boundary_gap(finals, boundary)
        if gap is None:
            failed = True
            print(f"INCONCLUSIVE GAP across {boundary:.0f}s boundary")
        elif gap > MAX_BOUNDARY_GAP:
            failed = True
            print(f"SUSPECT GAP {gap:.1f}s across {boundary:.0f}s boundary")
        else:
            print(f"PASS GAP {gap:.1f}s across {boundary:.0f}s boundary")
    print("done")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
