# Windows WASAPI Native Companion Implementation Plan (Gate G7A)

**Status:** Approved Technical Plan for Gate G7A  
**Date:** 2026-08-21  
**Governing Architecture:** `docs/architecture/0003-native-capture-launch-boundary.md` (ADR 0003) & `docs/plans/2026-08-13-native-capture-launch-roadmap.md`  
**Target Platform:** Windows 11 (Build 22H2+ / x64 and ARM64)

---

## 1. Executive Summary & Core Invariant

The Windows companion delivers 100% architectural parity with the macOS companion (`TarsNativeCompanion`) by implementing zero-friction, native audio capture on Windows 11.

### Non-Negotiable Invariants:
1. **NO Virtual Audio Devices**: Strictly NO VB-CABLE, Virtual Audio Cable, PyAudioWPatch, or Audio Repeater. No driver installations or system reboots required.
2. **Native Dual-Channel WASAPI Architecture**:
   - **Candidate Audio (System)**: WASAPI Loopback Capture on the active render endpoint (`AUDCLNT_STREAMFLAGS_LOOPBACK`).
   - **Recruiter Audio (Microphone)**: WASAPI Capture on the hardware communications endpoint.
3. **Canonical 50ms Audio Framing**: Linear 16-bit PCM Int16 sampled at 16,000 Hz mono, chunked into 50ms frames (1,600 bytes) with zero-copy bounded custody (maximum 2-second residency).
4. **Binary Wire Framing**: Identical binary WebSocket packet format: `[4-byte big-endian header length] + [JSON header] + [Raw Int16 PCM payload]`.

---

## 2. Technical Stack & Solution Architecture

### A. Technology Selection
- **Framework**: .NET 8.0 LTS (C# 12)
- **Audio Interop**: `NAudio.Wasapi` / `CoreAudioApi` with direct P/Invoke into `Mmdevapi.dll` and `Avrt.dll` (Multimedia Class Scheduler Service for low-latency MMCSS threading).
- **Executable Output**: Single self-contained native binary `tars-companion.exe` (`PublishSingleFile=true`, `PublishTrimmed=true`).

### B. Subsystem Components (`companion/native-windows/`)

```text
companion/native-windows/
├── TarsNativeWindows.sln
├── src/
│   ├── TarsNativeCompanion/
│   │   ├── Contracts/
│   │   │   ├── AudioFrame.cs
│   │   │   ├── SourceIdentity.cs
│   │   │   ├── CoverageGap.cs
│   │   │   └── CaptureHealth.cs
│   │   ├── Capture/
│   │   │   ├── WasapiLoopbackSystemAudioSource.cs   # System/candidate audio tap
│   │   │   ├── WasapiMicrophoneAudioSource.cs       # Recruiter mic tap
│   │   │   └── AudioResampler.cs                    # 48kHz/44.1kHz float -> 16kHz Int16
│   │   ├── Memory/
│   │   │   ├── CustodyRing.cs                       # 2-second bounded ring buffer
│   │   │   └── SecureBuffer.cs                      # Zeroization on delete
│   │   ├── Protocol/
│   │   │   ├── FrameReducer.cs                      # Dual-channel synchronizer
│   │   │   └── WebSocketAudioSink.cs                # ClientWebSocket transport
│   │   └── Security/
│   │       └── WindowsCredentialStore.cs            # DPAPI / Windows Credential Vault
│   └── TarsCompanionCLI/
│       └── Program.cs                               # CLI entry point (tars-companion.exe)
└── tests/
    └── TarsNativeCompanion.Tests/
        ├── CustodyRingTests.cs
        ├── FrameReducerTests.cs
        ├── ResamplerTests.cs
        └── WireProtocolTests.cs
```

---

## 3. Detailed Component Specifications

### 3.1. WASAPI Loopback Capture (`WasapiLoopbackSystemAudioSource.cs`)
- Uses `IMMDeviceEnumerator` to acquire `eRender` default endpoint (`eMultimedia` or `eCommunications`).
- Initializes `IAudioClient` with `AUDCLNT_STREAMFLAGS_LOOPBACK` and event-driven buffer callback.
- Feeds raw render buffer directly into `AudioResampler`.
- Detects endpoint route changes (e.g. unplugging headphones) via `IMMNotificationClient` and triggers automatic reconnect or explicit coverage gap reporting (`CoverageGap(reason: .device_lost)`).

### 3.2. WASAPI Microphone Capture (`WasapiMicrophoneAudioSource.cs`)
- Uses `IMMDeviceEnumerator` to acquire `eCapture` default communications endpoint.
- Initializes `IAudioClient` in capture mode with `AUDCLNT_STREAMFLAGS_EVENTCALLBACK`.
- Converts capture buffer to canonical Int16 PCM and chunks into 50ms frames.

### 3.3. Audio Resampling & Format Normalization (`AudioResampler.cs`)
- Meeting applications typically render in 48kHz stereo Float32.
- High-efficiency linear interpolation / polyphase FIR downsampling to 16,000 Hz 16-bit Int16 mono.
- Bounded CPU overhead (< 1% on modern Intel/AMD/Snapdragon X CPUs).

### 3.4. Transport & Wire Protocol (`WebSocketAudioSink.cs`)
- Manages persistent connection to `ws://{gateway}/api/stream/native/{session_id}`.
- Serializes packets matching the exact gateway specification:
  - Header: `{"session_id": "...", "source": "microphone" | "system_audio", "sequence": N, "first_sample": S, "sample_rate": 16000, "channel_count": 1, "duration_ms": 50}`
  - Big-endian 4-byte length prefix.
  - Raw binary PCM payload.
- Emits ping heartbeats every 15 seconds and receives pong acknowledgements.

---

## 4. Verification & Hard Anchor Gates

| Gate Phase | Test Matrix | Success Criteria |
| :--- | :--- | :--- |
| **Unit & Memory Tests** | `dotnet test` (xUnit) | 100% pass on RingBuffer, Resampler, Reducer, and Protocol Framing. |
| **Offline Conformance** | Shared vectors from `companion/protocol/vectors` | Identical hash outputs for identical fixture frames. |
| **Loopback Validation** | Local meeting playback (YouTube / Teams / Zoom) | Clean candidate transcription without recruiter mic leakage. |
| **End-to-End Gateway Test** | `tars-companion.exe` against local FastAPI gateway | Ingestion verified in `/api/stream/native/{session_id}` without dropped sequences. |

---

## 5. Next Steps for Implementation
1. Scaffold `companion/native-windows/` project files using .NET 8.
2. Port the core domain models (`AudioFrame`, `CustodyRing`, `FrameReducer`) from Swift/Python to C#.
3. Implement `WasapiLoopbackSystemAudioSource` and `WasapiMicrophoneAudioSource`.
4. Execute offline vector tests and end-to-end integration against the FastAPI test gateway.
