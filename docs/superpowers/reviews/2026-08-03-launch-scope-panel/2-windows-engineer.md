# Windows Platform Report — T.A.R.S. Launch Scoping

## 1. Windows system-audio capture options (verified against current docs)

**WASAPI loopback (whole-device).** Any shared-mode render endpoint can be captured by opening a capture stream with `AUDCLNT_STREAMFLAGS_LOOPBACK` on the render device. Per [Microsoft's loopback docs](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording): "A client can enable loopback mode only for a shared-mode stream... Exclusive-mode streams cannot operate in loopback mode," and event-driven loopback works since Windows 10 1703. No virtual device needed — this is BlackHole without BlackHole, structurally eliminating the "wrong Multi-Output selection silently captures nothing" failure. No consent prompt is documented for loopback; mic capture is gated separately (§3).

**Per-process loopback.** [`AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`](https://learn.microsoft.com/en-us/windows/win32/api/audioclientactivationparams/ne-audioclientactivationparams-audioclient_activation_type) captures one process tree (include or exclude mode). The [ApplicationLoopback sample](https://github.com/microsoft/Windows-classic-samples/blob/main/Samples/ApplicationLoopback/README.md) "requires Windows 10 build 20348 or later" — practically Windows 11, since client Win10 tops out at build 19045. Two properties matter for us: "The capture is not tied to a specific audio endpoint" (survives headset switching by construction — the Windows twin of the macOS Phase-1C route-recovery problem, solved structurally) and it delivers silence, rather than stopping, when the target process renders nothing.

**PortAudio/sounddevice — the existing backend does NOT get loopback for free.** Upstream PortAudio has no WASAPI loopback ([feature request #668, open](https://github.com/PortAudio/portaudio/issues/668)) and python-sounddevice's [#281 has been open since 2020](https://github.com/spatialaudio/python-sounddevice/issues/281). So `backend/audio/capture.py` works on Windows for the mic channel but cannot see any loopback device.

**Python alternatives that do work.** [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) — a PortAudio fork with WASAPI loopback, [wheels for Python 3.7–3.13](https://pypi.org/project/PyAudioWPatch/) — enumerates loopback analogues of render devices; closest to drop-in. [`soundcard`](https://github.com/bastibe/SoundCard/issues/25) does loopback natively but has open issues that matter here: [no data when playback starts silent (#166)](https://github.com/bastibe/SoundCard/issues/166) and [frame-rate/duration anomalies (#137)](https://github.com/bastibe/SoundCard/issues/137). Verdict: Python-level loopback is feasible today via PyAudioWPatch — whole-system only, not per-process.

## 2. Fastest credible path to Windows at launch

**(a) Existing Python backend + PyAudioWPatch: ~1–2 weeks.** `capture.py` is a thin class pushing numpy chunks into an asyncio queue; add a WASAPI-loopback source for the system channel, keep sounddevice for mic. Everything downstream (STT, labels, frontend) is untouched. Risks: single-maintainer PortAudio fork; whole-system loopback only; Python runtime per machine (fine hand-configured). Later cost: disposable bridge — ADR 0001's target is a native companion anyway.

**(b) Thin native C# companion speaking protocol 0002: ~3–5 weeks.** [NAudio's `WasapiLoopbackCapture`](https://github.com/naudio/NAudio/blob/main/Docs/WasapiLoopbackCapture.md) + `WasapiCapture` cover both channels in little code; protocol 0002 already has two-language conformance tests — add a C# suite. Caveat: `DataAvailable` won't fire while the device is silent (play silence to keep cadence, per NAudio docs). Per-process loopback isn't in NAudio ([#878](https://github.com/naudio/NAudio/issues/878)) but is reachable via COM interop later. This is the commercial-grade path; its cost now is the launch window, with macOS work competing for the same engineer.

**(c) Shared-core shell (Tauri/Electron/MAUI): 6+ weeks, buys nothing.** Capture is native per-OS either way, and T.A.R.S.'s UI is already the web frontend.

**Recommendation: (a) for launch, (b) as immediate fast-follow** (§5).

## 3. Distribution & trust for Ella-internal

- **Don't buy an EV cert.** EV no longer grants instant SmartScreen reputation — [Microsoft removed EV OIDs from the Trusted Root Program in Aug 2024](https://www.todesktop.com/blog/posts/windows-apps-psa-ev-certs-do-not-grant-immediate-reputation-anymore). The current sane option is [Azure Artifact Signing (formerly Trusted Signing)](https://azure.microsoft.com/en-us/products/artifact-signing), [$9.99/mo Basic](https://azure.microsoft.com/en-in/pricing/details/trusted-signing/) — though a [March 2026 intermediate-CA migration caused SmartScreen regressions](https://learn.microsoft.com/en-us/answers/questions/1850140/new-ev-code-signing-certificate-stored-in-azure-ke) for some publishers, so signing ≠ zero warnings.
- **For 3–10 hand-configured machines: skip signing at launch.** SmartScreen "More info → Run anyway" once per machine, or IT allowlists the binary. Inno Setup or zip+script is adequate; revisit signing/MSIX for commercial.
- **Mic permission:** one global toggle — ["Desktop apps cannot be individually toggled, but access for those apps can be controlled using Let desktop apps access your microphone"](https://support.microsoft.com/en-us/windows/windows-camera-microphone-and-privacy-a83257bc-e990-d54a-d212-b5e41beba857). One-time setup check per machine; loopback has no equivalent gate.

## 4. Windows-specific gotchas for this exact product

- **Cross-channel bleed, not loopback echo, is the attribution killer.** The recruiter's voice isn't in loopback unless the meeting app self-monitors (rare); the real risk is speakers — the mic then captures candidate audio, poisoning per-source speaker labels. Mandate headsets. Windows 11 has capture-side AEC ([`IAcousticEchoCancellationControl`](https://learn.microsoft.com/en-us/windows/win32/api/audioclient/nn-audioclient-iacousticechocancellationcontrol), sample needs build 22540+) if speakers must ever be supported.
- **Whole-system loopback captures everything** — notification dings, other apps — polluting the candidate channel/transcript. Per-process loopback is the clean fix; Windows 11-only, native-code-only.
- **Exclusive-mode render streams can't be loopback-captured** ([docs](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)) — rare on office machines, but log it as a detectable failure.
- **Silence gap:** device loopback delivers no packets when nothing renders; keep STT keepalives and "no audio" detection independent of packet cadence.
- **Headset switch mid-interview:** WASAPI streams are endpoint-bound; on default-device change the app must handle [`IMMNotificationClient::OnDefaultDeviceChanged` and reopen streams](https://learn.microsoft.com/en-us/windows/win32/coreaudio/stream-routing-implementation-considerations). Budget this explicitly — same failure class Phase-1C exists for on macOS.
- **Live UI:** keep it in the browser at launch. Any future native overlay must declare [Per-Monitor V2 DPI awareness or Windows bitmap-stretches it blurry on mixed-DPI multi-monitor setups](https://learn.microsoft.com/en-us/windows/win32/hidpi/high-dpi-desktop-application-development-on-windows).

## 5. Launch scope recommendation

**Must:** path (a) — sounddevice mic + PyAudioWPatch loopback; device pickers with a live level-meter "audio is flowing" check per channel (the #1 silent-failure killer); Windows 11 verified per machine; headsets mandated; hand-install with mic toggle confirmed.
**Should:** device-change detect + auto-reopen; scripted install; start companion (b) immediately post-launch.
**Cut:** per-process loopback, signing/MSIX, native overlay UI, Windows 10 ([out of support since Oct 14, 2025](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/windows-10-support-has-ended-on-october-14-2025)).

**Biggest risk:** PyAudioWPatch loopback quality over long sessions (drift, device-change behavior, fork maintenance). Mitigate with a 2-hour real-call soak test in week 1; if it fails, pivot to (b) and slip ~1 week — the NAudio path is well-trodden.

**Owner questions:** (1) Are all recruiter machines Windows 11, and can IT confirm builds? (2) Which meeting apps on Windows — browser Meet vs desktop Teams/Zoom (determines per-process loopback value)? (3) Is a headset policy enforceable? (4) Is a Python runtime install acceptable internally, or does IT require a single signed executable even for Ella machines?
