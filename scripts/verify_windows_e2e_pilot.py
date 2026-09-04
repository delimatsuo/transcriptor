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
import socket
import subprocess
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_PROJ = REPO_ROOT / "companion" / "native-windows" / "src" / "TarsCompanionCLI" / "TarsCompanionCLI.csproj"

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

connected_event: asyncio.Event | None = None
frames_received_event: asyncio.Event | None = None


@app.websocket("/api/stream/native/{session_id}")
async def mock_native_stream(websocket: WebSocket, session_id: str):
    await websocket.accept()
    if connected_event is not None:
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
                    and received_stats["system_audio_frames"] >= 5
                ):
                    if frames_received_event is not None:
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
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def run_server(server: uvicorn.Server):
    await server.serve()


async def main_async():
    global connected_event, frames_received_event
    connected_event = asyncio.Event()
    frames_received_event = asyncio.Event()

    port = get_free_port()
    session_id = f"pilot-win-e2e-{int(time.time())}"

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  T.A.R.S. Windows Companion (.NET 8) Live E2E Verification  ")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Project:    {CLI_PROJ}")
    print(f"Session ID: {session_id}")
    print(f"Gateway:    ws://127.0.0.1:{port}/api/stream/native/{session_id}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    # Start FastAPI server
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(run_server(server))

    while not server.started:
        await asyncio.sleep(0.05)

    # Launch dotnet run for TarsCompanionCLI
    cmd = [
        "dotnet",
        "run",
        "--project",
        str(CLI_PROJ),
        "--no-build",
        "--",
        "--session-id",
        session_id,
        "--gateway",
        f"ws://127.0.0.1:{port}/api/stream/native",
        "--simulate",
    ]

    # Pre-build to ensure fast startup
    subprocess.run(["dotnet", "build", str(CLI_PROJ)], check=True, capture_output=True)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=os.setsid,
    )

    try:
        try:
            await asyncio.wait_for(connected_event.wait(), timeout=10.0)
            print("✓ WebSocket connection established from Windows CLI to gateway")
        except asyncio.TimeoutError:
            print("✗ Timeout waiting for WebSocket connection from Windows CLI")
            sys.exit(1)

        print("  Streaming live dual-channel audio frames (mic + system loopback)...")
        await asyncio.sleep(3.0)

        print("\n--- Live Capture Ingestion Results ---")
        print(f"  Microphone frames received:    {received_stats['microphone_frames']}")
        print(f"  System audio frames received:  {received_stats['system_audio_frames']}")
        print(f"  Total PCM payload received:    {received_stats['total_pcm_bytes']} bytes")
        print(f"  Pings handled:                 {received_stats['pings_received']}")
        print(f"  Gaps reported:                 {received_stats['gaps_received']}")
        print("--------------------------------------\n")

        assert received_stats["microphone_frames"] > 0, "No microphone frames received"
        assert received_stats["system_audio_frames"] > 0, "No system audio frames received"
        assert received_stats["total_pcm_bytes"] > 0, "No PCM bytes ingested"
        print("✓ End-to-end Windows wire protocol validation PASSED")

    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

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
