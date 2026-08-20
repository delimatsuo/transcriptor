"""Manual soak: run live capture >2 rotations; verify no transcript gap.

Usage: .venv/bin/python3 -m backend.scripts.soak_rotation
Speak continuously (read aloud / play a podcast into the mic) for ~10 min.
The script prints every final segment with the provider-derived result end;
afterwards it verifies that the client delivered contiguous audio into all three
provider streams and that Chirp 3 returned final transcript results on both
sides of each rotation. Callback arrival intervals are not audio gaps.
"""

import asyncio
import time

from backend.audio.buffer import AudioBuffer
from backend.audio.capture import AudioCapture
from backend.config import get_settings
from backend.schemas.models import TranscriptSegment
from backend.stt.stream_manager import StreamManager

MAX_DELIVERY_GAP = 0.2
MAX_TRANSCRIPT_WINDOW = 45.0


def transcript_boundary_window(
    finals: list[TranscriptSegment], boundary: float
) -> tuple[float, float] | None:
    """Return provider-result lags immediately before and after a boundary."""
    ends = sorted(segment.end_time for segment in finals if segment.is_final)
    before = [end for end in ends if end <= boundary]
    after = [end for end in ends if end > boundary]
    if not before or not after:
        return None
    return boundary - max(before), min(after) - boundary


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
        input_channel=settings.microphone_input_channel,
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

    print("\n--- rotation delivery and transcript evidence ---")
    failed = not drain_completed
    if not drain_completed:
        print(f"SUSPECT DRAIN FAILURE {mgr.drain_failure_reason}")
    delivery_intervals = mgr.audio_delivery_intervals
    for stream_id, start, end in delivery_intervals:
        print(f"DELIVERY {stream_id} {start:.3f}-{end:.3f}s audio")
    if len(delivery_intervals) != 3:
        failed = True
        print(f"INCONCLUSIVE STREAM COUNT {len(delivery_intervals)} expected 3")

    for previous, current in zip(delivery_intervals, delivery_intervals[1:]):
        boundary = current[1]
        delivery_gap = max(0.0, current[1] - previous[2])
        if delivery_gap > MAX_DELIVERY_GAP:
            failed = True
            print(
                f"SUSPECT DELIVERY GAP {delivery_gap:.3f}s "
                f"from {previous[0]} to {current[0]}"
            )
        else:
            print(
                f"PASS DELIVERY GAP {delivery_gap:.3f}s "
                f"from {previous[0]} to {current[0]}"
            )

        window = transcript_boundary_window(finals, boundary)
        if window is None:
            failed = True
            print(f"INCONCLUSIVE TRANSCRIPT WINDOW at {boundary:.1f}s")
        elif max(window) > MAX_TRANSCRIPT_WINDOW:
            failed = True
            print(
                f"SUSPECT TRANSCRIPT WINDOW before={window[0]:.1f}s "
                f"after={window[1]:.1f}s at {boundary:.1f}s"
            )
        else:
            print(
                f"PASS TRANSCRIPT WINDOW before={window[0]:.1f}s "
                f"after={window[1]:.1f}s at {boundary:.1f}s"
            )
    print("done")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
