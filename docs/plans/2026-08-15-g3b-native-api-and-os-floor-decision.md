# G3B Native macOS API and OS-Floor Decision

Status: documentation-only decision record. It does not authorize source
implementation, permission prompts, device access, capture, network, provider,
credential, deployment, real audio, candidate data, or release activity.

## Exact decision anchor

- merged base: `47fc798885be4d09d983d16ddc14c26a1c90d366`;
- merged base tree: `160584b1fd9ddcb436a9c158f8658a6c6415fda3`;
- G3A merge parent: `bfc4d9b78f80635a7562f76f5c182890d672fa73`;
- G3A tree: `0fa6e032c7444a438d2be334d5efa1f111c04a35`;
- plan source: merged PR #12 head
  `23ea02d88292f900ea69e14c9cf917948703f36a`.

## Selected native boundary

1. Microphone capture uses `AVAudioEngine` and its input node through an
   adapter that exposes only generated, typed frames to the reducer. Device
   selection and route health use the Core Audio HAL identity supplied by the
   user/OS; the implementation must reject an unavailable or ambiguous device
   rather than silently selecting a new default route.
2. System-audio capture uses `ScreenCaptureKit` `SCStream` audio output with
   `capturesAudio` enabled and no display/video output. The adapter must not
   create, activate, document, or fall back to BlackHole, VB-CABLE, PyAudioWPatch,
   aggregate devices, or another virtual/default-route path.
3. The supported macOS floor is **macOS 13.0**. The floor is selected because
   the reviewed system-audio path requires the ScreenCaptureKit audio capture
   surface; availability checks fail closed on older systems. The microphone
   adapter does not lower the product floor.
4. Permission, route, interruption, sleep/wake, and device-loss observations
   are companion-owned state. They never become gateway coverage, transcript,
   completion, deletion, or provider-forwarding claims.

## Rejected alternatives

- BlackHole, VB-CABLE, PyAudioWPatch, virtual or aggregate devices;
- automatic default-route switching or hidden fallback capture;
- display/video capture, screen-frame retention, or OCR as a system-audio path;
- provider SDKs, cloud APIs, WebSockets, credentials, or endpoint selection; and
- a lower OS floor that would require a different or unreviewed audio path.

## Later verification requirements

The later G3B source package must compile and exercise generated fixtures with
networking and live device access disabled. It must bind API availability,
permission-denial, route-loss, interruption, sleep/wake, overflow, bounded
custody, local discard, deletion, and late-callback tests to the exact source
tree. A controlled physical-device fixture run requires a separate authorization
and does not follow from this decision.
