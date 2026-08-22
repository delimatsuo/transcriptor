#!/usr/bin/env python3
"""
Wire-format harness ONLY: connects the companion to an in-script mock gateway.
Proves packet framing, NOT capture, NOT transcription, NOT launch readiness.
For live proof see scripts/verify_live_system_audio.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANION_BIN = REPO_ROOT / "companion" / "native-macos" / ".build" / "release" / "tars-companion"

app = FastAPI()

received_stats = {
    "microphone_frames": 0,
    "system_audio_frames": 0,
    "total_pcm_bytes": 0,
    "pings_received": 0,
    "pongs_sent": 0,
    "gaps_received": 0,
    "sequences": set(),
}

connected_event = asyncio.Event()
frames_received_event = asyncio.Event()


@app.websocket("/api/stream/native/{session_id}")
async def mock_native_stream(websocket: WebSocket, session_id: str):
    await websocket.accept()
    connected_event.set()

    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            if "bytes" in msg and msg["bytes"]:
                raw: bytes = msg["bytes"]
                if len(raw) < 4:
                    continue
                header_len = int.from_bytes(raw[:4], byteorder="big")
                if len(raw) < 4 + header_len:
                    continue
                header_bytes = raw[4 : 4 + header_len]
                pcm_bytes = raw[4 + header_len :]

                try:
                    header = json.loads(header_bytes.decode("utf-8"))
                except Exception:
                    continue

                source = header.get("source", "microphone")
                seq = header.get("sequence", 0)
                received_stats["sequences"].add(seq)
                received_stats["total_pcm_bytes"] += len(pcm_bytes)

                if source == "microphone":
                    received_stats["microphone_frames"] += 1
                elif source == "system_audio":
                    received_stats["system_audio_frames"] += 1

                if (
                    received_stats["microphone_frames"] >= 5
                    or received_stats["system_audio_frames"] >= 5
                    or (received_stats["microphone_frames"] + received_stats["system_audio_frames"]) >= 10
                ):
                    frames_received_event.set()

            elif "text" in msg and msg["text"]:
                try:
                    data = json.loads(msg["text"])
                    if data.get("type") == "ping":
                        received_stats["pings_received"] += 1
                        await websocket.send_json({"type": "pong"})
                        received_stats["pongs_sent"] += 1
                    elif data.get("type") == "gap":
                        received_stats["gaps_received"] += 1
                except Exception:
                    pass
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        pass


def get_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def run_server(server: uvicorn.Server):
    await server.serve()


async def main_async():
    port = get_free_port()
    session_id = f"pilot-e2e-{int(time.time())}"

    # Ensure binary exists
    if not COMPANION_BIN.exists():
        print(f"Building companion binary: {COMPANION_BIN}...")
        build_res = subprocess.run(
            ["swift", "build", "-c", "release"],
            cwd=str(REPO_ROOT / "companion" / "native-macos"),
            capture_output=True,
            text=True,
        )
        if build_res.returncode != 0:
            print(f"Build failed:\n{build_res.stderr}")
            sys.exit(1)

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  T.A.R.S. End-to-End Pilot Integration Verification        ")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Binary:     {COMPANION_BIN}")
    print(f"Session ID: {session_id}")
    print(f"Gateway:    ws://127.0.0.1:{port}/api/stream/native/{session_id}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    # Start FastAPI server in background task
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(run_server(server))

    # Wait for server to start
    while not server.started:
        await asyncio.sleep(0.05)

    # Launch tars-companion process
    cmd = [
        str(COMPANION_BIN),
        "--session-id",
        session_id,
        "--gateway",
        f"ws://127.0.0.1:{port}/api/stream/native",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=os.setsid,
    )

    try:
        # Wait for connection
        try:
            await asyncio.wait_for(connected_event.wait(), timeout=5.0)
            print("✓ WebSocket connection established from tars-companion to gateway")
        except asyncio.TimeoutError:
            print("✗ Timeout waiting for WebSocket connection from tars-companion")
            sys.exit(1)

        # Stream for 3 seconds
        print("  Streaming live audio frames...")
        await asyncio.sleep(3.0)

        # Print statistics
        print("\n--- Live Capture Ingestion Results ---")
        print(f"  Microphone frames received:    {received_stats['microphone_frames']}")
        print(f"  System audio frames received:  {received_stats['system_audio_frames']}")
        print(f"  Total PCM payload received:    {received_stats['total_pcm_bytes']} bytes")
        print(f"  Pings handled:                 {received_stats['pings_received']}")
        print(f"  Gaps reported:                 {received_stats['gaps_received']}")
        print("--------------------------------------\n")

        total_frames = received_stats["microphone_frames"] + received_stats["system_audio_frames"]
        assert total_frames > 0, "No audio frames received from live companion"
        assert received_stats["total_pcm_bytes"] > 0, "No PCM bytes ingested"
        print("✓ End-to-end wire protocol validation PASSED")

    finally:
        # Terminate companion process gracefully
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        # Stop server
        server.should_exit = True
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    print("✓ Graceful teardown complete")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
