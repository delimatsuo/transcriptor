# Task 11 builder report — source/offline qualification

## Exact Task 11 path accounting

The Task 11 full diff contains 16 paths: one frozen brief and 15
builder-authored paths. The brief is listed for accounting only and was not
edited.

1. `docs/builder/task-11-brief.md` — frozen brief
2. `scripts/verify_live_system_audio.py`
3. `scripts/release_menubar_app.sh`
4. `companion/native-macos/Sources/TarsNativeCompanion/CaptureSource.swift`
5. `companion/native-macos/Sources/TarsNativeCompanion/ProcessTapSystemAudioSource.swift`
6. `companion/native-macos/Sources/TarsNativeCompanion/ScreenCaptureKitSystemAudioSource.swift`
7. `companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift`
8. `companion/native-macos/Sources/TarsCompanionApp/AppDelegate.swift`
9. `companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift`
10. `companion/native-macos/Tests/TarsNativeCompanionTests/CompanionSessionControllerTests.swift`
11. `scripts/live_system_audio_harness.py`
12. `scripts/test_live_system_audio_harness.py`
13. `companion/native-macos/Sources/TarsNativeCompanion/LiveHarnessProtocol.swift`
14. `companion/native-macos/Sources/TarsNativeCompanion/LiveHarnessControl.swift`
15. `companion/native-macos/Tests/TarsNativeCompanionTests/LiveHarnessTests.swift`
16. `docs/builder/task-11-report.md`

Frozen brief SHA-256:
`8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.

An earlier reviewer-bound full-diff SHA-256 (not the final7 starting state) was
`c7a148e52f2eff82fae9e74a57ed57a78240e3ac5ea6efcfcb004703c620a0cb`.
The supplied Final8 starting exact-diff SHA-256 for this bounded repair was
`b3ba4a40b1df5f86de76b55d47c4797cdf8e7ac8d969244ba22d129a7d145652`.
The supplied Final9 starting exact-diff SHA-256 for this bounded repair was
`5b61c58caed30f939a1c48cb0f1eaeced84501b2c6c6d3e9b4ed464d2fc9e2ad`.
The supplied Final10 starting exact-diff SHA-256 for this bounded repair was
`7b500b2f97058223183311e597dc18737da2b101247dc00d5afc4c2bbdaf86db`.
The supplied Final10a starting exact-diff SHA-256 for this bounded repair was
`a2976363dc239481fcd2112219f20e759238f5fed042245fef9fb0534c27b76c`.
The Final11 starting exact-diff SHA-256 for this bounded repair was
`5cb02cf637c29546d155fee0448db648f94ea1c94577171666b0f185ccb958f2`.
The supplied Final11a starting exact-diff SHA-256 for this bounded follow-up
was
`0a755552eea63399c8f3527ba86986d28a8d3c616c46274552c2028d7944ae19`.
The supplied Final11b starting exact-diff SHA-256 for this bounded follow-up
was
`89638a2c536eba98427401b7743b08056ac09bce9f08d341af16e6e0efcc2669`.
The supplied Final11d starting exact-diff SHA-256 for this bounded follow-up
was
`038880947f8b4b583f5dbe1591b628d78bf0b9505f6a12f2ea1799e53b9d6c4c`.
The supplied Swift Final6 review was **BLOCKED** on terminal-denial parity,
terminal cleanup capability, the signed-app digest self-reference, and the
missing executed fake-release proof. Those four findings were the bounded
Final11 repair scope; the Python Final6 review was approved for the existing
offline state.
The final post-repair binary-diff SHA-256 is intentionally left pending
external read-only rebinding. No Git mutation occurred; this report does not
claim that Git was never inspected read-only.

## Repairs and causal evidence

1. Darwin peer identity now obtains eUID through libc `getpeereid`, uses
   `LOCAL_PEERPID=0x002` and `LOCAL_PEERTOKEN=0x006`, and rejects every PID or
   audit-token read whose returned size is not exactly 4 or 32 bytes. The
   Darwin-only local socket boundary test exercises the actual reader and
   checks eUID, current PID, 32-byte token, current executable path, and the
   constants.
2. Every Swift control descriptor installs `SO_NOSIGPIPE` before any send.
   The isolated fork/socketpair test closes the peer, performs the real
   descriptor write with the production send flags, and proves the child exits
   normally with EPIPE/control-loss behavior rather than SIGPIPE termination.
3. `HarnessState` retains a frozen complete activation identity per generation.
   Health is accepted only when peer, session, nonce, attempt, generation,
   source object, observer token, and all three engine identities match. The
   Python table-driven test rejects one hostile mutation per field and keeps
   the typed positive predicate false.
4. `UnixHarnessServer` queues every decoded event frame per connection and
   drains the queue in wire order. A same-process AF_UNIX fixture coalesces
   activation and health in one write, receives both frames successively, and
   establishes the functional-permission positive path; duplicate session
   command ownership remains rejected.
5. `LaunchServicesProcess` retains immutable full `PeerIdentity` plus a live
   revalidator bound to the still-open authenticated socket. After bind,
   lifecycle operations revalidate the full peer and living `/usr/bin/open
   -W` helper as defense in depth, while TERM/KILL cross the destructive
   boundary only through the exact 32-byte `LOCAL_PEERTOKEN` passed to the
   token-only `proc_signal_with_audittoken` call. There is no `pid_probe`,
   `os.kill`, or integer-PID signal path; the kernel-bound token carries the
   process identity/pid-version, so a reused integer PID cannot become the
   target and a stale token is rejected. The causal token-only and
   mutation-effective tests reject PID-bearing substitutions and stale-token
   sends. `CompanionRun` performs full-peer binding immediately after accepted-
   socket admission.
6. Evidence uses the active stream-key sentinel before any retained write,
   projects through a fixed allowlist, and derives Markdown dynamic fields
   only from accepted canonical evidence. Production-shaped transcript,
   phase-detail, and error sentinel fixtures force FAIL and retain zero
   sentinel bytes. PASS remains available only from the typed positive
   predicate.
7. The report preserves the 16-path accounting and frozen-brief distinction,
   records the starting digest and pending final rebinding, and separates
   current offline source evidence from inherited or later live/release proof.
8. Python control reads now treat each bounded socket deadline as a polling
   interval while the authenticated descriptor remains open. Teardown passes
   a stop callback and uses a short bounded slice so closing the socket can
   interrupt the reader; EOF, OSError, decoder, schema, and identity errors
   still revoke the session and all positive facts. The production-shaped
   AF_UNIX test keeps activation and granted health positive across four quiet
   deadlines, then proves peer EOF revocation.
9. `phase_evidence` observes `CompanionRun.secret_seen` immediately before
   evaluating the typed proof. A live CompanionRun fixture sends a unique
   stream-key sentinel split across two output chunks only after initial
   readiness; the redacted output/log/phase rows/Markdown retain zero sentinel
   bytes and the evidence result is FAIL with no positive claim.
10. The policy exposes one typed `functional_health` predicate and applies it
    to every schema-valid health event. Only the current activation tuple with
    `running`/`granted`/`healthy`/`clear`/`awake`, `overflowed=false`, and a
    valid device identity grants functional permission; terminal or degraded
    updates overwrite prior grants with `unknown` (or `denied` for
    `denied`/`revoked`). Table cases cover every requested unsafe dimension,
    and source mutation checks prove each safety conjunct is necessary.
11. Accepted terminal source failures now emit one fenced canonical `failed`
    health event while the exact source/token/generation/attempt fence still
    exists, then perform destructive cleanup. The exact approved monitor
    permission-denial message is the only `failed` case serialized as
    `permission=denied`; every other failed status is `unknown`. A typed
    permission denial thrown during start emits one fenced `failed`/`denied`
    event without fabricating activation, while terminal failures may bypass
    activation buffering and ready/running/stopped positive-eligible events
    remain activation-first. Causal tests cover activated denial, nonpermission
    failure, and typed startup denial, including cleanup and no-positive-claim
    assertions.
12. `LiveHarnessControlCoordinator` starts authenticated control-loss polling
    before invoking the controller start owner. EOF, duplicate/trailing bytes,
    or event-writer loss cancels the suspended start and awaits MainActor stop;
    a real socketpair test holds start at a gate, proves stop before gate
    release, then proves the stale return cannot publish activation or revive
    the session.
13. The Swift app continues to authenticate the server by effective UID only,
    as required by the frozen brief. Same-host/same-user account compromise is
    explicitly outside this boundary; no stronger client identity or server
    authentication claim is made by this repair.
14. `HarnessState` admits a schema-valid activation-less `failed` health only
    after the authenticated command/session fence is bound, and only with the
    strict non-granting `unknown`/`denied` permission tuple. It binds the full
    event identity as a sticky terminal, never creates an activation, rejects
    later revival, and preserves the typed denial in `CompanionRun`, which
    retires the control cleanly without a generic `event_error` reset. Real
    AF_UNIX wire cases cover activation-less denied and unknown failures,
    hostile combinations, nonfailed preactivation rejection, and no-positive-
    claim/no-revival behavior.
15. Swift `LiveHarnessEvent` encoding/decoding and the Python decoder enforce
    the same failed-permission biconditional: `denied` is allowed only with the
    exact approved denial message, `unknown` must not carry it, and granted or
    revoked are invalid for failed health. Canonical valid unknown/denied and
    all hostile combinations are covered by cross-language-shaped tests.
16. `CompanionSessionController` retains a source only after exact
    system-audio and concrete-engine identity attestation. Identity-missing and
    identity-mismatch tests use weak references and controller inspection to
    prove the rejected source/sink are not retained and no provider/capture
    side effect or activation occurs.
17. `LiveHarnessControlCoordinator` now has a real waiter-ready rendezvous and
    sole-waiter ownership. Writer failures request Darwin shutdown to wake the
    waiter; only the owner closes after the waiter returns, and the coordinator
    cancels/stops a suspended start before joining both tasks. Real socketpair
    cases cover EOF, trailing bytes, writer-side shutdown, duplicate waiters,
    stop-before-release, stale-start joining, and no revival/activation. The
    ownership test also verifies the numeric descriptor remains valid after an
    active-waiter close request, survives 256 temporary descriptor pairs with
    no reuse, and reaches `EBADF` only after the owner close; the peer shutdown
    is used solely as the deterministic EOF completion edge.
18. `LiveHarnessControlCoordinator` now gives each scheduled start task a
    cancellation-aware `StartOwnership` entry gate. A control loss observed
    before task entry remains pending until the task crosses that edge, then
    denies/cancels it, awaits controller stop, and joins both waiter and start
    owners. `CompanionSessionController.start` has early cancellation and
    attempt checks before attempt creation, sink retention, observer setup, and
    the first sink/source side effects. The deterministic pre-entry-loss test
    proves the entry edge occurs, stop follows it, source/sink never start or
    connect, no activation/session revival occurs, and the coordinator joins.
19. Terminal failure parity is now preserved through the Python phase
    boundary: the exact approved denial message returns `tcc`, records
    `BLOQUEADO`, and follows the existing exit-code policy to **42** without a
    `FAIL`; every other schema-valid terminal failure remains `falhou` and
    records `FAIL`. A causal phase test exercises both outcomes over a real
    AF_UNIX session.
20. `_retire_terminal_failure` now clears success facts but retains the exact
    authenticated descriptor and complete peer binding until `stop()` has
    sent the exact 32-byte audit token, observed helper completion, joined the
    event and output readers, and retired the socket/listener once. A
    real-socket/fake-process test proves one token-only TERM and one close;
    malformed post-admission control loss now retains that authority through
    the authenticated TERM/EOF cleanup edge instead of detaching first.
21. Task 11 artifact digests are now signature-neutral. The signed-app branch
    hashes the unsigned packaged executable before embedding the provenance
    resource, signs the bundle once afterward, and verifies the final signed
    executable by stripping only a task-scoped copy through the injectable
    `codesign --remove-signature` boundary before hashing. The Python signed
    artifact inspector applies the same disposable-copy rule and never edits
    the signed original.
22. The release proof now executes a temporary-repository fake command runner,
    rather than only scanning source. It creates an unsigned fixture app,
    mutates the executable during final bundle signing, restores the unsigned
    payload for signature-neutral readback, checks strict metadata/entitlements
    and resource-before-sign ordering, and proves no
    security/notarytool/hdiutil/stapler/spctl/open/say/provider command runs. A
    raw post-sign digest mutation fails the test. The fake matrix also rejects
    the exact audio entitlement with `<false/>`, a lookalike key, an unexpected
    extra entitlement, and hostile same-team Apple Development authority.
23. Python `PERMISSION_DENIED_MESSAGE` is compared byte-for-byte with
    `SystemAudioCaptureMonitor.permissionDeniedMessage` by an offline Swift
    test that parses the real Python source; mutating the Python literal makes
    that comparison fail. No additional source path was introduced.
24. After the active-waiter ownership test reaches its owner close and
    `EBADF`, it forces a replacement socket onto the exact old numeric fd and
    invokes stale close/shutdown/send operations. The replacement channel
    remains operational, proving post-close fd-reuse safety and idempotent
    stale-object behavior.
25. Swift initial command framing and each event write now use one monotonic
    absolute transaction deadline. Each read/write syscall receives only the
    remaining budget; event writes use nonblocking send plus deadline-bounded
    writable polling so repeated partial progress cannot reset the transaction.
    Durable post-command control-loss polling remains an unbounded sequence of
    short idle intervals. Real socketpair tests drip command fragments and
    slowly drain a filled event socket; both prove the total elapsed bound and
    are mutation-effective against the former per-call timeout behavior.
26. Final11b now requires authenticated control EOF for every connected stop,
    including an already-completed or graceful process. A missing EOF leaves
    the descriptor/listener and owner retained for a later causal retry; only
    process/helper completion plus EOF, joined readers, and one owner close can
    claim cleanup. The fresh artifact reinspection failure fixture proves zero
    credential bytes, exactly one token-only SIGTERM, one connection close,
    listener/run-directory retirement, and no retained owner. Preflight and
    final evidence use typed clean provenance snapshots, and finalization
    requires the exact JSON boolean `transcription_complete=true`.
27. Final11c keeps authenticated lifecycle ownership after generic
    post-admission protocol faults: `_revoke_control` revokes activation and
    functional permission immediately but retains the accepted descriptor,
    peer PID, audit-token binding, and connection until bounded TERM/KILL,
    exact peer revalidation, peer EOF, and one teardown. Authenticated EOF is
    a fail-closed passive branch: an alive `open -n -W` helper refuses cleanup,
    while helper completion after EOF permits retirement without signaling.
    Causal fixtures cover malformed-event TERM/EOF, helper-live refusal,
    helper-completion success, and explicit fixture-owned disposal for
    deliberately unprovable negative cases; the harness run is warning-clean.
28. Final11c propagates the exact `SignedArtifactInspector` and expected
    head/tree/digest into the restart replacement `CompanionRun`. Restart
    session transmission therefore performs fresh signed-artifact inspection
    immediately before credentials; a mutation fixture proves zero
    replacement session-frame bytes and retains/retries cleanup ownership.
29. Harness-mode app termination is now an injected seam gated by one valid
    authenticated command. The coordinator finishes, the observer is removed,
    and the control connection is closed before the termination closure runs;
    menu-bar launches have no harness client and retain normal lifetime.
30. Final11d makes restart inspection authoritative to the original
    `CompanionRun`: `phase_restart_drill` accepts no independent inspector,
    derives the run-bound object, and fails closed before replacement launch
    when that binding is absent. The mutation fixture uses the same inspector
    for the original sealed read and replacement drift read, then proves
    zero replacement command bytes with explicit peer-readiness, drift,
    token-only stop, helper-completion, server-EOF, peer-reader completion,
    and zero-byte edges. A missing-inspector fixture proves no replacement
    launch or send.
31. Final11d requires exact zero exit status from `codesign --verify`,
    `codesign -dv`, and entitlement readback before parsing any output;
    matching-looking diagnostics with a nonzero status are rejected by
    mutation-effective offline cases.
32. An independent review identified an unresolved P1: path-only artifact
    reinspection does not attest that the already-accepted audit-token-bound
    process is the code approved by the artifact facts if the bundle path is
    swapped and restored. No unsafe substitute was added in this bounded
    turn. A follow-up design packet must specify the injectable
    Security.framework dynamic-code check (audit-token guest lookup,
    designated requirement/CDHash/signing metadata comparison, and a final
    token reread) plus a swap/restore zero-byte fixture before this proof can
    claim process-code identity.

## Exact offline verification

- `PYTHONWARNINGS=error::ResourceWarning python3 -X tracemalloc=5 -m unittest
  discover -s scripts -p 'test_live_system_audio_harness.py' -q`:
  **56/56 passed**, including terminal denied/unknown phase parity, retained
    token-only cleanup, signature-neutral artifact inspection, the executable
    fake release runner, activation-less denied/unknown terminal wire failures,
    exact permission parity, four-deadline AF_UNIX liveness, late split-chunk
    redaction, functional-health revocation, authenticated EOF/helper
    completion, harness-only termination, authoritative mutated-restart
    zero-byte tests, and missing-inspector launch rejection.
  The combined stdout/stderr capture contained **zero** `ResourceWarning:` and
  **zero** `unclosed <socket.socket` occurrences; ResourceWarnings were
  promoted to errors and tracemalloc was enabled.
- The mutated-restart test passed **30/30** consecutive isolated iterations;
  every iteration reported `status=PASS`, with zero failures.
- The explicit warning-fixture subset (peer binding, post-admission revoke,
  both activationless terminal subtests, three-idle-deadline EOF, and
  functional-health revocation) ran **5/5 passed** under the same warning
  gate, with zero `ResourceWarning:` and zero `unclosed <socket.socket`.
- `PYTHONWARNINGS=error::ResourceWarning python3 -X tracemalloc=5 -m unittest
  discover -s scripts -p 'test*.py' -q`:
  **56/56 passed** (the packaged-artifact module has no discovered unittest
    cases in this environment; the evidence fixture's printed FAIL is an
    intentional negative assertion and the unittest run is green).
- `python3 -m py_compile scripts/live_system_audio_harness.py
  scripts/verify_live_system_audio.py scripts/test_live_system_audio_harness.py`:
  **passed**.
- `bash -n scripts/release_menubar_app.sh`: **passed**.
- `shellcheck scripts/release_menubar_app.sh`: **passed** (ShellCheck was
  available).
- `swift test --disable-sandbox --package-path companion/native-macos
  --filter LiveHarnessTests`:
  **33/33 passed**, including Python/Swift denial parity, activation-less failure/parity, source-retention,
    waiter-rendezvous, pre-entry loss, suspended-start ownership, and absolute
    initial-read/event-write deadline cases, plus executable current-attempt UUID
    and launch-nonce drift rejection at the real controller callback edge,
    and the harness-only termination seam. `--disable-sandbox` is required by
    this restricted offline runner because SwiftPM's manifest sandbox is denied;
    no app was launched.
- The exact descriptor ownership test passed **100/100** consecutive isolated
  runs after the deterministic peer-EOF completion edge was added. Earlier
  stress runs exposed a scheduling-sensitive waiter completion (one failure at
  iteration 27, with the waiter still open); that transient drove the bounded
  test-only completion synchronization and is not counted as final green.
- `swift test --disable-sandbox --package-path companion/native-macos` (debug):
  **232/232 passed**.
- `swift test --disable-sandbox -c release --package-path companion/native-macos`
  (optimized): **232/232 passed**.
- `swift build --disable-sandbox -c release --product TarsCompanionApp
  --package-path companion/native-macos` (release app build): **passed**;
  `tars-companion` and `TarsCompanionApp` built without
  launching the app.
- The full Swift runs include the mutation-effective generation and
  AppDelegate source-bound guards; removing either guard is required to make
  its corresponding assertion fail. No mutation was left in the checkout.
- The parent-supplied, not rerun in this bounded loop, baselines remain
  **backend 361/361** and **frontend 64/64**. The release app build was rerun
  above and passed; it is not live app execution evidence.

## Scope, non-actions, and proof ceiling

The current Task 11 diff is exactly the 16 paths enumerated above: one frozen
brief plus 15 builder paths. The frozen brief remains byte-for-byte unchanged
at SHA-256
`8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.

The preserved Task 11 repair's changed subset is exactly these nine existing
builder paths (this describes the full preserved repair, including Final11c,
not only this report):
`scripts/verify_live_system_audio.py`,
`scripts/live_system_audio_harness.py`,
`scripts/test_live_system_audio_harness.py`,
`scripts/release_menubar_app.sh`,
`companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift`,
`companion/native-macos/Sources/TarsNativeCompanion/LiveHarnessProtocol.swift`,
`companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift`,
`companion/native-macos/Tests/TarsNativeCompanionTests/LiveHarnessTests.swift`,
and this report. `LiveHarnessControl.swift` and the other Task 11 paths are
preserved from the earlier bounded repair and were not claimed as new edits in
this expanded subset. No write occurred outside the authorized Task 11 paths.

Final11c directly edited only these six allowlisted paths: the three Python
files `scripts/verify_live_system_audio.py`,
`scripts/live_system_audio_harness.py`, and
`scripts/test_live_system_audio_harness.py`; the harness app source
`companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift`; its
source-bound test `companion/native-macos/Tests/TarsNativeCompanionTests/LiveHarnessTests.swift`;
and this report.

Final11d directly edited only these three allowlisted paths:
`scripts/verify_live_system_audio.py`,
`scripts/test_live_system_audio_harness.py`, and this report. No Swift,
frozen-brief, or provider/live files were edited in this follow-up.

The supplied starting binary-diff SHA-256 for this final expanded repair was
`89638a2c536eba98427401b7743b08056ac09bce9f08d341af16e6e0efcc2669`.
The supplied Final11d starting exact-diff SHA-256 was
`038880947f8b4b583f5dbe1591b628d78bf0b9505f6a12f2ea1799e53b9d6c4c`.
The final exact binary-diff SHA-256 remains **pending external read-only
rebinding**; the final report SHA-256 is returned with this handoff after the
report bytes settle. No Git mutation occurred.

Current executed checks in this bounded loop are the **56/56 Python harness
tests**, **33/33 focused Swift LiveHarnessTests**, **232/232 full Swift debug
tests**, **232/232 full Swift optimized tests**, Python compilation, shell
syntax/ShellCheck, and the release app build listed above. The final combined
Python capture had zero `ResourceWarning:` and zero `unclosed <socket.socket`
occurrences under `PYTHONWARNINGS=error::ResourceWarning` with tracemalloc.
Parent-supplied
baselines not rerun in this loop remain **backend 361/361** and **frontend
64/64**; they are not relabeled as current execution evidence. Earlier Swift
Final6 review was BLOCKED on terminal-denial parity, cleanup capability,
signature-neutral digesting, and executable fake-release proof; those findings
are the bounded repair history, not additional current failures.

No app or `/usr/bin/open` was launched. No live audio, TCC operation, process
enumeration or external process control, real credential/Keychain operation,
signing/timestamping/notarization/DMG/stapling/Gatekeeper operation, network,
provider, deployment, or production operation was performed. Tests used only
injected launch/process behavior, temporary fake bundles, isolated local
AF_UNIX fixtures, and the authorized isolated child/socketpair.

This qualifies source behavior, canonical wire/schema behavior, and offline
local fixtures only. It does not qualify real LaunchServices admission,
live Process Tap PCM, TCC permission, signed/notarized artifact execution,
the accepted process's dynamic code-signature identity (the P1 follow-up in
item 32),
provider/STT behavior, performance, privacy/legal handling, deployment, or
release readiness. Those remain later proof gates.

## Final12 dynamic-code binding addendum

This bounded follow-up closes the confirmed swap-before-launch and
restore-before-static-reinspection gap without changing the sealed
`Task11Provenance` schema or the release script. The supplied starting
full-index binary-diff SHA-256 was
`9eddc1b24e86c127bbc4b5ccf9b37bba338a99dbcc55e0eb7b4eb6184ae693ae`; the
frozen brief remained unchanged at
`8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
The final exact-diff digest remains pending parent read-only rebinding.

`StaticCodeIdentity` now retains only bounded, immutable raw bytes for the
Security.framework unique code identity (1..64 bytes) and designated
requirement (1..65536 bytes). `SignedArtifactInspector` derives it only after
the existing codesign static policy readback through the injectable reader;
fresh pre-send inspection compares the complete `ArtifactFacts`, including
that identity. `CompanionRun` requires and retains one attestor before
creating its listener or launching, and restart reuses that exact inspector,
attestor object, and frozen identity. The Darwin bridge uses the absolute
bundle CFURL, static validity flags `0x19`, exported Security/CoreFoundation
keys, audit-token CFData of exactly 32 bytes, guest lookup by
`kSecGuestAttributeAudit`, kernel designated-requirement validity, exact raw
unique comparison, and a second dynamic validity check. Created/copied CF
objects are released exactly once in reverse acquisition order; borrowed
dictionary values and exported constants are not released.

Credential admission is now ordered as accepted full peer, process binding,
descriptor publication, fresh complete static inspection/equality, same-
socket peer reread, dynamic attestation, final same-socket peer reread, exact
accepted/reread/final token equality, and only then command encoding,
state admission, and `sendall`. A failed dynamic/static/final-token gate
sends zero command bytes, leaves `artifact_valid` false and command state
empty, and retains the authenticated descriptor/process owner for the
existing token-only TERM/KILL/EOF cleanup boundary. The Swift control client
sets `FD_CLOEXEC` using preserved `F_GETFD` flags and
`F_SETFD(existing | FD_CLOEXEC)` immediately after `socket()` and before `connect()`; injected
connections enforce the same idempotent operation.

The new mutation-effective offline coverage includes typed identity bounds and
pre-listener rejection, fake Security-reader/attestor injection, a raw fake
CoreFoundation/Security bridge with exact static/dynamic call ordering,
32-byte audit-data validation, malformed type/length/status/null rejection,
and reverse-order CF ownership assertions, dynamic static-A/dynamic-B
swap-and-restore rejection over **30/30 iterations**, same-PID/different-token
final reread rejection, and zero-byte/token-only cleanup assertions. Source
guards reject PID/fallback guest lookup, nil dynamic requirements, and command
send before the final reread. Existing harness coverage supplies the
authenticated EOF, attestation-failure cleanup, restart ownership, and other
command-order invariants; the fake bridge path does not load live
Security.framework.

Verification completed in this worktree:

- Strict Python full discovery, captured twice after the final code change with
  `TMPDIR=/private/tmp`, `PYTHONWARNINGS=error::ResourceWarning`, and
  `-X tracemalloc=5`: **61/61 passed** on each run. Combined captures had
  zero `ResourceWarning:` and zero `unclosed <socket.socket` occurrences.
- Targeted Final12 identity/dynamic tests: **5/5 passed**, including the raw
  fake Security bridge, source-order guard, 30-iteration swap/restore matrix,
  and final-token mutation.
- Python `py_compile` for all seven `scripts/*.py` files: **passed** with
  bytecode directed to a writable temporary prefix.
- Focused `LiveHarnessTests`: **36/36 passed**.
- Full Swift debug package: **235/235 passed**.
- Full Swift release package: **235/235 passed**.
- Release `TarsCompanionApp` product build: **passed**; no app was launched.
- `bash -n` for all six shell scripts and ShellCheck for the same six:
  **passed**.

The evidence ceiling is source, offline injected Security-boundary behavior,
local AF_UNIX mutation fixtures, Swift descriptor behavior, and compilation.
No live Security.framework attestation, signed artifact execution, process
inspection/control, audio/TCC, provider, network, deployment, production, or
release proof was performed. No Git staging, commit, push, or write outside
the six allowlisted paths occurred.

## Final13 allocator and closeout addendum

This source/offline-only closeout records the final ABI and lifecycle repairs:
`SecRequirementCopyData` uses its three-argument ABI with `flags=0`; all
CoreFoundation creation calls pass the documented explicit `NULL` allocator
without reading or exporting `kCFAllocatorDefault`; and the real
`CFDictionaryKeyCallBacks` and `CFDictionaryValueCallBacks` structures are
used for the `kCFTypeDictionary*CallBacks` exports. The bridge retains strict
raw `StaticCodeIdentity` attestation, performs the final peer reread inside
the same send transaction before any command bytes, and treats EOF observed
before stop/kill as passive completion while retaining the authenticated
owner until helper completion.

Root independently observed the post-repair Python suite at **64/64 passed
twice** and the focused Final13 set at **6/6 passed**. These are root
verification results, not builder verification. In this worktree, the
post-repair focused ABI/raw-bridge and source-shape tests passed **2/2**, and
one strict full Python discovery pass passed **64/64**. Fresh reviewer
results and the final exact-tree digest remain pending parent read-only
rebinding.

The evidence ceiling remains source, offline injected Security-boundary
behavior, and local fixtures only. No live Security.framework, signing,
application launch, TCC, Process Tap/audio, provider, network, cloud,
production, or release proof is claimed. No Git operation was performed.

## Final14 strict-boundary and lifecycle repair addendum

Final14 started from the parent-supplied exact HEAD
`5ea4e703cf6c4d6beb958b0946539d3127ff5066` and unstaged full-index diff digest
`6779d7bee84be7b257001f8e3866bec5da305d95c38e0ff664482d2a628d38e4`.
The frozen `docs/builder/task-11-brief.md` remained unchanged at SHA-256
`8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
Final fresh review and the post-repair exact-tree/diff digest are pending parent
read-only rebinding.

This bounded repair addresses all five confirmed Final13 blockers:

1. `require_exact_static_code_identity` rejects every subclass at the static
   reader, artifact validation, dynamic attestor, and final send boundaries,
   validates raw immutable bytes, and compares both CDHash and designated
   requirement with `hmac.compare_digest`. `PeerIdentity` likewise uses exact
   type and explicit eUID/PID/token/path comparisons at the final send and
   lifecycle revalidation edges. Artifact facts are exact-typed at validation
   and compared field-by-field on fresh inspection. Forged equality subclasses
   therefore send zero command bytes and leave command/artifact state unset;
   ordinary exact values still pass.
2. Harness-mode app entry now owns an idempotent
   `LiveHarnessLifecycleFinalizer` seam in `LiveHarnessControl.swift`. It awaits
   source/sink stop, clears the observer, joins queued event writes and closes
   the connection owner, then invokes termination exactly once. The app calls
   that seam for open/receive/nonce/coordinator success or failure and for a
   malformed harness invocation with no client. Normal non-harness mode never
   starts the runtime and never reaches termination. An offline Swift
   socketpair/EOF lifecycle test and source-order/mutation checks were added;
   SwiftPM could not execute them in this sandbox (see verification below).
3. `SignedArtifactInspector` now requires exactly one CodeDirectory detail line
   and uses bounded `re.search` for its single `flags=... hashes=...` token,
   accepting only valid runtime tokens. Missing, standalone, non-runtime,
   malformed, duplicate, and ambiguous flags fail closed. Fake fixtures use
   the runtime-shaped CodeDirectory line and mutation cases.
4. Retained Markdown emits the positive ceiling only for canonical `PASS` plus
   exact `process-tap-positive`. `FAIL`, `BLOCKED`, and `INCONCLUSIVE` each get
   an explicit non-positive conclusion and no positive live-capture prose.
5. `StreamingRedactor.finish()` treats every non-empty pending sentinel prefix
   as secret material, drops it, and sets the violation bit. Complete, split,
   and every terminal prefix length are covered while ordinary suffixes remain.

Current offline verification in this worktree:

- `PYTHONWARNINGS='error::ResourceWarning,error::DeprecationWarning' TMPDIR=/private/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts python3 -X tracemalloc=5 -m unittest discover -s scripts -p 'test*.py' -q`: **67/67 passed**, run twice. Both captures contained zero `ResourceWarning`, zero `DeprecationWarning`, and zero `unclosed <socket.socket>` output. This includes forged identity/peer zero-wire tests, exact artifact type tests, real CodeDirectory plus mutation fixtures, all terminal redactor prefixes, and non-PASS Markdown assertions.
- Temporary-prefix `python3 -m py_compile scripts/live_system_audio_harness.py scripts/verify_live_system_audio.py scripts/test_live_system_audio_harness.py`: **passed**.
- `swift test --disable-sandbox --scratch-path <fresh /private/tmp path> --package-path companion/native-macos --filter LiveHarnessTests`: **blocked before compilation** by SwiftPM/compiler `permissionDenied`; no Swift assertion executed. No Xcode, app, helper, Security.framework, process-control, signing, or live workaround was attempted.

The evidence ceiling is source inspection, Python compilation, offline fake
signature/identity boundaries, local AF_UNIX fixtures, and unexecuted Swift
source/test seams. No Git command or mutation, app/process launch or
enumeration/control, live Security.framework or codesign operation, audio/TCC,
provider/network/cloud, signing/Keychain, deployment, production, or release
action occurred. Final14 does not claim device, live Process Tap PCM, TCC,
provider/STT, performance, privacy/legal, or release qualification.

## Final15 launch, terminal-boundary, and evidence repair addendum

Final15 began from the parent-supplied exact HEAD
`5ea4e703cf6c4d6beb958b0946539d3127ff5066` and unstaged full-index diff digest
`e081485e1d027cb457590c307faa1e241c78a8e64389cfc32f930884177474b5`.
The frozen brief remained unchanged at SHA-256
`8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
Final15 review results and the post-repair exact-tree/diff digest remain
pending parent read-only rebinding.

This bounded repair closes the four confirmed Final14 findings and records
the requested Final15 verification/report boundary:

1. Harness startup now runs from the status-item label's launch-mounted
   `.onAppear`, before the lazily presented `MenuBarExtra` content. The
   production `task == nil, harnessMode` guard keeps repeated label
   appearances exactly once; normal mode installs only the join handler and
   never starts or terminates the harness. Source/mutation contracts cover the
   label-only call site, absence of the content task, the once guard, and the
   unconditional malformed/pre-command finalizer seam.
2. `credential_material` and `redact_credential_material` define one
   fail-closed rule for the full active sentinel anywhere and every non-empty
   proper sentinel prefix at the end of a complete value. Streaming EOF,
   complete values, phase rows, facts, emitted output, nested keys and values,
   transcript/error projections, registration-time re-sanitization, rejected
   evidence fallback, `secret_free`, and canonical evidence all use that
   rule. Every prefix length plus the full sentinel is mutation-tested through
   stdout, rows, facts, transcript, nested mappings, canonical evidence,
   fallback, and final Markdown; any observed material sets the durable bit
   and cannot produce PASS. Ordinary non-terminal strings remain unchanged.
3. Python and Bash now require exactly one complete CodeDirectory line:
   `CodeDirectory v=<digits> size=<digits> flags=0x<hex>(runtime)
   hashes=<digits>+<digits> location=<nonspace>`, with exactly one flags token,
   an exact `runtime` label, and bit `0x10000` set. Zero/low runtime bits,
   literal flags, `notruntime`, missing/extra tokens, duplicate lines, and
   duplicate flags are rejected. The Bash parser is one reusable
   Bash-3.2-compatible function used at both existing signature readback
   sites. Offline inspector and fake-release matrices include the exact valid
   line and every demonstrated mutation; no `codesign` process was executed.
4. The production phase row is now the neutral
   `Companion — estado da captura Process Tap` for PASS, FAIL, BLOQUEADO, and
   INCONCLUSIVE. Full `phase_evidence` Markdown is tested for every outcome;
   non-PASS output contains none of the affirmative capture/ceiling phrases,
   while PASS retains only its bounded positive ceiling.
5. This addendum maps the repairs to A–E, records exact offline command
   counts and the SwiftPM permission gate, and leaves the final review verdict
   and exact-tree/diff digest for parent read-only rebinding.

Final15 verification performed in this worktree:

- Focused Python mutation set (`test_streaming_redactor_drops_every_terminal_secret_prefix_and_sets_violation`, `test_every_terminal_credential_boundary_redacts_rows_facts_stdout_and_markdown`, `test_production_phase_evidence_uses_neutral_companion_label_for_each_result`,
  `test_signed_artifact_inspector_and_prelaunch_gate_block_every_sealed_mismatch`,
  `test_signed_app_release_executes_signature_neutral_fake_runner`): **5/5
  passed**.
- Strict full Python discovery, run twice with
  `PYTHONWARNINGS='error::ResourceWarning,error::DeprecationWarning'`,
  `TMPDIR=/private/tmp`, `PYTHONDONTWRITEBYTECODE=1`,
  `PYTHONPATH=scripts`, and `python3 -X tracemalloc=5 -m unittest discover
  -s scripts -p 'test*.py' -q`: **69/69 passed per run**. The only printed
  warning was the pre-existing urllib3 LibreSSL `NotOpenSSLWarning`.
- `PYTHONPYCACHEPREFIX=<fresh /private/tmp directory> python3 -m py_compile
  scripts/live_system_audio_harness.py scripts/verify_live_system_audio.py
  scripts/test_live_system_audio_harness.py`: **passed**.
- `swiftc -frontend -parse companion/native-macos/Sources/TarsCompanionApp/TarsCompanionApp.swift companion/native-macos/Tests/TarsNativeCompanionTests/LiveHarnessTests.swift`:
  **passed**.
- Focused `swift test --disable-sandbox --scratch-path <fresh /private/tmp>
  --package-path companion/native-macos --filter LiveHarnessTests` and full
  Swift debug with the same fresh-scratch policy: **blocked before
  compilation** by SwiftPM/compiler `permissionDenied`; no Swift assertion
  executed.
- `bash -n scripts/release_menubar_app.sh; shellcheck
  scripts/release_menubar_app.sh`: **passed**. The fake signed-app release
  test exercised the exact valid CodeDirectory line and all parser mutations,
  while invoking no live release tool.
- A literal trailing-whitespace audit over the seven allowlisted paths:
  **passed**. `git diff --check` and all other Git commands were intentionally
  not run under the Final15 no-Git boundary.

The evidence ceiling remains source inspection, Swift syntax parsing, Python
compilation and offline tests, injected AF_UNIX fixtures, canonical evidence
mutation tests, and fake release/inspector boundaries. Final15 performed no
Git operation, app launch, popover or process enumeration/control, live audio,
TCC, Security.framework or `codesign` execution, signing, Keychain, network,
provider/cloud, deployment, production, or release action. Review verdicts,
exact-tree/diff digest, and any device/provider/production/privacy/legal or
performance qualification remain pending and unclaimed.

## Parent independent Final15 verification

The parent independently rebound the repaired tree and completed the broader
verification set. Fresh `/private/tmp` scratch paths allowed SwiftPM to execute;
this supersedes only the builder sandbox-specific `permissionDenied` gate and
does not expand the evidence ceiling:

- Strict Python suite under the `ResourceWarning` and `DeprecationWarning`
  error gates: **69/69 passed twice**.
- Focused `LiveHarnessTests`: **39/39 passed**.
- Full Swift debug: **238/238 passed**.
- Full Swift release: **238/238 passed**.
- Release `TarsCompanionApp` product build: **passed**.
- `bash -n` and ShellCheck: **passed**.
- Backend suite: **361/361 passed**.
- Frontend suite: **64/64 passed**; `tsc --noEmit` and the Next production
  build also **passed**.

These remain parent-provided independent results, not live qualification. The
source/offline evidence ceiling remains in force: no app/audio/TCC or process
control, live Security.framework or `codesign`, signing/Keychain, network,
provider/cloud, deployment, production, privacy/legal, device, or performance
proof is claimed. The exact final diff SHA-256 and fresh review verdict remain
pending parent read-only rebinding.

## Final16 gateway, reducer, ownership, and typed-redaction repair addendum

Final16 preserves the parent-bound starting HEAD
`5ea4e703cf6c4d6beb958b0946539d3127ff5066`, canonical pre-edit full-index diff
SHA `f3428a95aae9f29e707d0755677364c92dbb8a9412b0a895abe66e7c98c680f4`, and
the frozen brief SHA-256
`8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
Final16 edits are limited to the two native sources, the native focused test,
the two Python sources, the Python test, and this report. The final exact diff
SHA and independent review verdict remain pending parent read-only rebinding.

The four requested repairs are implemented as follows:

1. F1 — `LiveHarnessGatewayBase` and Python `validate_gateway_base` now share
   one keyless absolute `ws`/`wss` grammar: canonical nonempty host, bounded
   port/path, ASCII-only syntax, and rejection of userinfo, query, fragment,
   whitespace/control, backslash, percent/ambiguous encoding, and raw or
   encoded stream-key admission. Construction, encoding, decoding, and the
   controller's send boundary validate it; gateway failures use constant
   diagnostics and never echo gateway/error material. Hostile cross-language
   matrices cover query/userinfo/fragment/path and raw/encoded key smuggling.
2. F2 — required phase outcomes are reduced exhaustively with fail-closed
   malformed/unknown/missing statuses and priority `FAIL > BLOCKED >
   INCONCLUSIVE > PASS`. A positive proof plus any non-PASS row cannot produce
   a positive result, claim, or Markdown ceiling.
3. F3 — derived `process_tap_positive` and
   `process_tap_evidence_result` facts are operational ledger fields only and
   are rejected by the retained canonical fact projection. Markdown validates
   its top-level shape, result/claim pairing, and canonical fact allowlist;
   hostile noncanonical affirmative inputs are rejected.
4. F4 — producer-owned structural keys and exact controlled enum values use
   full-sentinel handling, while row names/details, unknown statuses/speakers,
   arbitrary nested keys/values, errors, transcripts, and all other dynamic
   strings use the full sentinel plus every nonempty terminal proper-prefix
   rule. Exact phase/transcript mapping kinds prevent subset spoofing. Dynamic
   redaction sets the durable violation bit, keeps fallback documents
   structurally valid, and prevents PASS. Regression coverage includes the
   unknown status ending a real prefix and every first character of the
   URL-safe sentinel alphabet.

Final16 offline verification performed in this worktree:

- Focused Python F1–F4/mutation set (gateway validator, all terminal
  redaction boundaries, neutral phase outcomes, nested fallback, canonical
  ownership, and exhaustive reducer): **7/7 passed**.
- Strict Python suite with
  `-W error::ResourceWarning -W error::DeprecationWarning`,
  `TMPDIR=/private/tmp`, `PYTHONDONTWRITEBYTECODE=1`, and `PYTHONPATH=scripts`:
  **72/72 passed twice**. The only printed warning was the pre-existing
  urllib3 LibreSSL `NotOpenSSLWarning`. This includes the fake release-parser
  matrix; no release tool or `codesign` process was executed.
- `PYTHONPYCACHEPREFIX=<fresh /private/tmp path> python3 -m py_compile
  scripts/live_system_audio_harness.py scripts/verify_live_system_audio.py
  scripts/test_live_system_audio_harness.py`: **passed**.
- `swiftc -frontend -parse` on the touched Swift source and focused test:
  **passed**. A fresh `/private/tmp` SwiftPM scratch attempt was blocked
  before compilation by the host compiler metadata `permissionDenied` error;
  no Swift assertion executed in this worktree.
- `bash -n scripts/release_menubar_app.sh` and ShellCheck: **passed**.
- Literal trailing-whitespace scan across the seven allowlisted paths:
  **passed**. No Git command or digest computation was performed.

The evidence ceiling remains source, syntax/compilation checks, offline typed
fixtures, local AF_UNIX tests, canonical evidence mutation tests, and fake
release/inspector boundaries. No app/audio/TCC or process enumeration/control,
live Security.framework or `codesign`, signing/Keychain, network,
provider/cloud, deployment, production, privacy/legal, device, or performance
action or qualification is claimed. Parent exact-diff rebinding and fresh
review remain pending.

## Parent independent Final16a verification

Final16a changed only `companion/native-macos/Tests/TarsNativeCompanionTests/LiveHarnessTests.swift`, marking the hostile gateway controller test `@MainActor` to satisfy Swift actor isolation while preserving every assertion.

The parent independently rebound starting HEAD
`5ea4e703cf6c4d6beb958b0946539d3127ff5066`, an empty index, exactly 16
authorized changed paths, and the frozen brief SHA-256
`8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
The pre-report-update canonical `git diff --full-index HEAD --` SHA-256 was
`1dc1c5630c14f17cbc993744f866aadc88257d506ec2204f83f57ccd04ffcb71`.

Parent verification passed: strict Python **72/72 twice**; focused
`LiveHarnessTests` **41/41**; Swift debug **240/240**; Swift release
**240/240**; release `TarsCompanionApp` product build; backend **361/361**;
frontend **64/64**, plus `tsc` and the Next production build; `py_compile`,
`bash -n`, ShellCheck, and `git diff --check`. The only Python warning was the
pre-existing urllib3 LibreSSL `NotOpenSSLWarning`.

These are source/offline and parent test/build results only. The evidence
ceiling remains source inspection, syntax/compilation checks, offline typed
fixtures, local AF_UNIX tests, canonical evidence mutation tests, and fake
release/inspector boundaries. No app/audio/TCC/process-control,
Security.framework or `codesign` execution, signing/Keychain,
network/provider/cloud/deployment/production/device/performance qualification
is claimed. Final post-report digest and fresh Sol/Terra review remain pending;
no live qualification is claimed.

## Final17 consolidated P2 repair addendum

Final17 addresses two independent fresh-review P2 RED findings against the
parent-bound candidate (HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`,
pre-edit canonical full-index diff SHA
`3f9e95f2fb9517d77003589139037a00ccd3b52fe1acff5dd5b6444a3d639019`, frozen
brief SHA-256
`8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`).

1. **P2-A — typed evidence/provenance:** the prior normal production-shaped
   path falsely marked **10/64** URL-safe stream-key first characters as
   credential material because arbitrary phase/fact text shared the dynamic
   terminal-prefix rule. Final17 adds closed `PhaseID`/`PhaseStatus` and typed
   producer phase rows/details, an explicit `FactSpec` ownership table, and
   tagged `CredentialReachableDiagnostic` handling for free text. Restart
   ownership is a dedicated non-retained slot. Production transcript text is
   consumed ephemerally and is replaced by closed speaker/word/hit/count and
   restart-match facts; raw transcript cannot enter canonical evidence or
   Markdown. Structural keys/closed producer values use full-sentinel
   collision handling, while dynamic keys/values retain full plus every
   terminal proper-prefix rule and the durable no-PASS bit.
2. **P2-B — failed-health wire schema:** the prior failed-event schema
   accepted raw sentinel-bearing `message` text on the authenticated wire.
   Final17 removes arbitrary `message` from Swift/Python health schemas and
   replaces it with the symmetric closed codes `permission-denied` and
   `capture-failed`, enforcing `(permission == denied) == (code ==
   permission-denied)`. The controller converts local source diagnostics to
   the typed event status before construction; redacted fields and framed
   payloads contain no local failure text or stream key. Unknown/missing,
   extra, message-bearing, sentinel-bearing, and permission/code-mismatch
   mutations reject.

Final17 edited exactly these seven allowlisted paths:

- `companion/native-macos/Sources/TarsNativeCompanion/LiveHarnessProtocol.swift`
- `companion/native-macos/Sources/TarsNativeCompanion/CompanionSessionController.swift`
- `companion/native-macos/Tests/TarsNativeCompanionTests/LiveHarnessTests.swift`
- `scripts/live_system_audio_harness.py`
- `scripts/verify_live_system_audio.py`
- `scripts/test_live_system_audio_harness.py`
- `docs/builder/task-11-report.md`

Final17 bounded offline verification:

- Focused Python failed-health schema smoke set: **3/3 passed**; focused
  Final16 redaction/evidence set: **5/5 passed**; Final17 typed ownership and
  failed-health mutation additions: **2/2 passed**.
- Full strict Python discovery under
  `TMPDIR=/private/tmp PYTHONPATH=scripts`
  `PYTHONWARNINGS='error::ResourceWarning,error::DeprecationWarning'`:
  **74/74 passed twice**. The only printed warning was the pre-existing
  urllib3 LibreSSL `NotOpenSSLWarning`.
- `PYTHONPYCACHEPREFIX=<fresh /private/tmp path> python3 -m py_compile`
  on the three Python paths: **passed**.
- Fresh `/private/tmp` SwiftPM focused `LiveHarnessTests`: **41/41 passed**
  after compiling the typed event-status and controller changes.
- `bash -n scripts/release_menubar_app.sh` and ShellCheck: **passed**;
  no release or `codesign` command was executed.
- A literal trailing-whitespace audit of this report: **passed**. No Git
  command or digest computation was performed.

The evidence ceiling remains source/offline: typed fixtures, mutation tests,
SwiftPM tests in fresh scratch, Python compilation, and static shell checks.
Final17 performed no app/audio/TCC or process enumeration/control,
Security.framework or `codesign` execution, signing/Keychain,
network/provider/cloud/deployment/production/device/performance action or
qualification. Parent final exact-diff rebinding and fresh Sol/Terra review
remain pending; no live qualification is claimed.

## Final17a narrow repair addendum

The parent independently reproduced two residual P2 findings on the Final17
candidate (current digest
`5130641af42dc5473ed7c78b919be9b04a98d5fcfd29d381d097dfef61d0114c`):

1. Ordinary post-key progress strings ended in the first character of the
   active stream key for `o` and `l`, so `Phases.emit` falsely set the durable
   secret bit and made an otherwise normal run fail.
2. A caller could pass
   `PhaseID.SESSION_CREATED, PhaseStatus.PASS, "caller-selected-prefix:S"`
   and obtain a PASS row without redaction for an `S`-starting key.

Final17a repairs those boundaries in the three allowlisted files
`scripts/verify_live_system_audio.py`,
`scripts/test_live_system_audio_harness.py`, and
`docs/builder/task-11-report.md`. `Phases.emit` now accepts only the tagged
`CredentialReachableDiagnostic`; settling, typed final/total segment counts,
and evidence-written output use a closed `ProgressNotice` enum and validated
integer fields. PASS rows now require exact `PhaseID`,
`PhaseStatus.PASS`, and the closed `PhaseDetail.template()` value. Every
production PASS branch uses that contract, while failure text is explicitly
diagnostic and remains terminal-prefix redacted. Positive FactSpec slots also
retain diagnostic wrappers dynamically and cannot support PASS.

Final17a bounded offline verification:

- Focused Final17a plus retained Final17 regression set: **8/8 passed**.
- Full strict Python discovery under
  `TMPDIR=/private/tmp PYTHONPATH=scripts`
  `PYTHONWARNINGS='error::ResourceWarning,error::DeprecationWarning'`:
  **77/77 passed**. The only printed warning was the pre-existing urllib3
  LibreSSL `NotOpenSSLWarning`.
- Fresh `/private/tmp` `PYTHONPYCACHEPREFIX` compilation of the three Python
  paths: **passed**.
- Report trailing-whitespace check: **passed**.

The Final17a evidence ceiling remains source/offline: typed fixture and
mutation tests, Python compilation, and static inspection. No app/audio/TCC
or process enumeration/control, Security.framework or `codesign` execution,
signing/Keychain, network/provider/cloud/deployment/production/device/
performance action or qualification occurred. Parent final rebinding,
post-report digest, and fresh independent review remain pending; no live
qualification is claimed.

## Parent independent Final17a verification

The parent independently rebound Final17a at starting HEAD
`5ea4e703cf6c4d6beb958b0946539d3127ff5066`, with an empty index and exactly
16 authorized changed paths. The frozen brief SHA-256 is
`8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`; the
pre-report-update canonical `git diff --full-index HEAD --` SHA-256 is
`7eb1331a3a8b782c9ae66261b8e9f76e4aa6061e5dd613492f183616a40e7833`.

Independent Final17a results: custom 64-character progress/raw-PASS/emit
mutation tests passed; strict Python **77/77 passed twice** under
`ResourceWarning` and `DeprecationWarning` error gates, with only the
pre-existing urllib3 LibreSSL `NotOpenSSLWarning` printed; Swift debug
**240/240** and release **240/240** passed; the release
`TarsCompanionApp` product build passed; backend **361/361** and frontend
**64/64** passed, with `tsc --noEmit` and the Next production build passed.
`py_compile`, `bash -n`, ShellCheck, `plutil lint` for both app
plist/entitlements, and `git diff --check` passed.

The final post-report digest and fresh Sol/Terra review remain pending. The
evidence ceiling is source/offline verification only: mutation tests, Swift
build/test results, Python compilation, static shell checks, and plist
linting. No live app/audio/TCC/process enumeration or control,
Security.framework or `codesign` execution, signing/Keychain,
network/provider/cloud/deployment/production/device/performance qualification
was performed or is claimed.
## Parent independent Final18 verification

- Starting HEAD: `5ea4e703cf6c4d6beb958b0946539d3127ff5066`; empty index; exactly 16 authorized paths.
- Brief SHA: `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Pre-report canonical full-index diff SHA: `ba7d3a9f0ca3e8dfe74adbd8fd264d01d9b1c8d58da3d1f6df52f30c998658f3`.
- Final18 fixes: exhaustive typed fact/phase ownership with a post-projection durable-secret check; protocol v2 domain-separated role bindings with no raw event session/source/observer/device carrier fields; exact failed-health schema; complete active-key rejection; and 64-character production-order plus raw/wrapper mutation tests.
- Parent verification: strict Python 81/81 twice with zero ResourceWarning/DeprecationWarning/unclosed markers (only the pre-existing urllib3 LibreSSL `NotOpenSSLWarning`); focused Swift `LiveHarnessTests` 43/43; full Swift debug 242/242; release 242/242; release `TarsCompanionApp` build passed; backend 361/361; frontend 64/64; TypeScript noEmit and Next production build passed; Python compile, Swift parse, bash -n, ShellCheck, plutil lint for both entitlements/plist, and git diff --check passed.
- The first full Swift replay found two invalid failed-health test fixtures. Luna corrected the test baseline only; focused, debug, and release runs then passed.
- Final post-report digest and fresh Sol/Terra review are pending.
- Evidence ceiling: source/offline only; no app/audio/TCC/process enumeration/control, live Security.framework/codesign, signing/Keychain, network/provider/cloud/deploy/production/device/performance proof.

## Parent independent Final19 verification

- Binding: branch `codex/task11-live-process-tap-source`; starting HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`; empty index; exactly 16 intended changed paths.
- Brief SHA-256: `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Pre-report canonical `git diff --full-index HEAD --` SHA-256: `8f94327020a4758c73a272864c70d51d200b79904997db91f9478cce7a15d0e1`.
- Final19 repairs: exact immutable positive Process Tap proof and exact success phase plan/final qualification seal; mandatory 43-character URL-safe producer key at Python/Swift command/event/redactor boundaries with prelaunch containment and private/revoked credential lifetime; domain-separated attempt/session bindings, UInt64 generation symmetry, IPv4-mapped IPv6 rejection, and exact restart predicate; independent canonical fact/row/list schema validation rejecting forged typed/container credential prefixes; and a Swift slow-drain fixture admitting a valid command before testing authenticated write timeout while retaining production pre-command send rejection.
- Parent verification: strict Python **81/81 twice** under `ResourceWarning` and `DeprecationWarning` error gates, with zero ResourceWarning, zero DeprecationWarning, and zero `unclosed <socket.socket>` markers; only the pre-existing urllib3 LibreSSL `NotOpenSSLWarning`. Direct mutation probes for exact proof, weak/42/44/invalid key grammar, mandatory keyed event API, >UInt64 generation, IPv4-mapped IPv6, empty phase-plan exit, forged typed phase-detail prefix, and arbitrary closed-list prefix all failed closed.
- Initial focused Swift 43-test run found exactly one deterministic invalid fixture expectation (pre-command send). The bounded fixture correction was applied; isolated **1/1** and focused `LiveHarnessTests` **43/43** then passed. Full Swift debug **242/242**, full Swift release **242/242**, release `TarsCompanionApp` product build, backend **361/361**, and fresh `.env*`-excluded frontend mirror **64/64** passed; `tsc --noEmit` and the Next production build passed. One earlier malformed TypeScript invocation printed compiler help and was discarded; the corrected fresh-mirror invocation passed. Python `py_compile` for 3 files; Swift frontend parse for all 10 changed Swift sources/tests; `bash -n`; ShellCheck; `plutil lint` for `Info.plist` and entitlements; and `git diff --check` passed.
- Final post-report digest and fresh exact-digest Sol/Terra reviews remain pending. Evidence ceiling: source/offline only. No app launch, live audio, TCC/device/process enumeration or control, Security.framework/codesign execution, signing/Keychain, provider/network/cloud/deploy/production/performance qualification, or live Task11 claim.

## Parent independent Final20 verification

- Binding remains branch `codex/task11-live-process-tap-source`, starting HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`, empty index, exactly 16 intended paths.
- Frozen brief SHA-256: `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Pre-report canonical `git diff --full-index HEAD --` SHA-256: `4c1188c7574430e6cd6957ebd2c92de512d4f656020853edaad6a6806e98bfd0`.
- Final20 closes Final19 reviewer findings with deterministic current-state/canonical/proof revalidation plus phase-ledger anti-transplant binding and per-mutation tests; internally minted and self-revalidated canonical evidence before Markdown; bounded domain-separated `pb1_<64 lowercase hex>` complete-peer binding suitable for restart identifiers; exact boolean cleanup and exact snapshot ownership; bounded canonical integers; redactor sentinel retirement after output-reader join; Swift raw-plus-derived value scanning before event retention; and atomic active-key retirement on shutdown so concurrent/late sends reject.
- Root verification after the final source edit: Python `py_compile` passed; strict Python discovery **90/90 passed twice** under ResourceWarning and DeprecationWarning error gates with tracemalloc; explicit adversarial Final20 causal subset **8/8 passed**; focused `LiveHarnessTests` **45/45**; full Swift debug **244/244**; full Swift release **244/244**; release `TarsCompanionApp` product build passed without launch; backend **361/361**; fresh `.env*`-excluded and protected-instruction-file-excluded frontend mirror **64/64**, TypeScript noEmit passed, Next production build passed; Swift frontend parse for all 10 changed Swift sources/tests, bash -n, ShellCheck, plist/entitlements plutil lint, and git diff --check passed.
- Initial strict Python failures were independently reduced to shared fixture/classification causes and one missing import, repaired by Luna, then the final matrix above passed.
- Evidence ceiling remains source/offline only. No app launch, live audio, TCC/device/process enumeration or control, Security.framework/codesign execution, signing/Keychain, provider/network/cloud/deploy/production/performance qualification occurred.
- Final post-report digest and fresh exact-digest Sol/Terra review are pending; no live qualification is claimed.

## Parent independent Final21 verification

- Binding: branch `codex/task11-live-process-tap-source`; starting HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`; empty index; exactly 16 intended paths; frozen brief SHA-256 `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Rejected Final20 reviewer-bound digest: `fdf21381114ff36aa50b743d9c8dbabe21499e071a54142d846ff9aef561e7b0`. Fresh Sol decision was BLOCK with one P1 and two P2; Terra had no separate findings. Final20 was not approved.
- Pre-report Final21 canonical `git diff --full-index HEAD --` SHA-256: `a3332395cc99f0e6bf3bde7357d2058652a14962844eb5c8f0b1831464d6e1b6`.
- Repairs: (1) CompanionRun lock-linearizes exact proof snapshot capture with activation, functional-health, accepted terminal, and control-revocation mutations; every post-snapshot event/control proof transition revokes the snapshot while normal lifecycle teardown alone preserves it. (2) Diagnostic FAIL fallback bounds exact ints to UInt64, sanitizes out-of-range top-level/nested values, and has a total minimal canonical FAIL fallback without weakening canonical positive rejection. (3) Swift actual write admission and complete write transaction require the expected active key and non-shutdown state under one lock; credential-free test hooks force shutdown to retire the key after framing but before the paused kernel shutdown syscall.
- Root causal evidence: new focused Python 2/2 passed with zero ResourceWarning/DeprecationWarning/unclosed markers; deterministic Swift shutdown race 1/1 passed and observed zero peer bytes/EAGAIN before kernel shutdown. In ignored temporary mirrors, deleting only terminal snapshot invalidation made the Python test fail on a retained LiveProofSnapshot, and deleting only the Swift in-write shutdown/key guard made the race test fail because send did not report controlLost. Both mutation mirrors were deleted and never touched the candidate.
- Full final baseline: strict Python **92/92 passed twice** under ResourceWarning and DeprecationWarning error gates with tracemalloc and zero warning/unclosed markers; focused `LiveHarnessTests` **45/45**; full Swift debug **244/244**; full Swift release **244/244**; release `TarsCompanionApp` build passed without launch; backend **361/361**; fresh `.env*`-excluded and protected-instruction-file-excluded frontend mirror **64/64**, TypeScript noEmit passed, Next production build passed; Python `py_compile`, Swift frontend parse for all 10 changed Swift sources/tests, bash -n, ShellCheck, plist/entitlements plutil lint, git diff --check all passed.
- Evidence ceiling remains source/offline only: no app launch, live audio/TCC/device/process enumeration or control, live Security.framework/codesign, signing/Keychain, provider/network/cloud/deploy/production/performance qualification.
- Final post-report digest and fresh exact-digest Sol/Terra review remain pending; no live qualification is claimed.



## Parent independent Final22 verification

- Binding: branch `codex/task11-live-process-tap-source`; starting HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`; zero staged paths; exactly 16 intended changed paths; frozen brief SHA-256 `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Rejected Final21 reviewer-bound full-index diff SHA-256: `4ae5f03ca2fd9cc9e87251ba37416843fc95596af8e3179c1a609308eee81746`. Final21 was BLOCKED: Sol reproduced a P1 real AF_UNIX control EOF / phase-stop scheduling race that retained a granted proof snapshot and returned EXIT_OK; Terra found P2 unbounded cyclic/deep diagnostic traversal and P2 actual peer-control-loss write retirement gap; Sol also required degraded/control-loss causal snapshot coverage.
- Pre-report Final22 canonical `git diff --full-index HEAD --` SHA-256: `cf352f777bed1444e1abf5179760b538f22db4d7044d09d180ef156f8f25fda0`.
- Repairs: bounded cycle/depth/node diagnostic projection with exact depth 64, safe marker, durable ownership failure, total minimal canonical FAIL Markdown; genuine control loss distinguished from intentional local reader stop, real loss invalidates snapshot even when stop wins, capture rejects `HarnessState.control_lost`; accepted degraded health gets causal snapshot revocation coverage; Swift non-timeout peer control loss atomically retires shutdown/key under lock before unwind with credential-free hook and queued-write zero-byte proof.
- Root focused causal evidence: Python 4/4 and Swift peer-control-loss race 1/1. Five disposable mutation variants were rejected: disabled EOF run invalidation, capture guard, degraded snapshot clear, exact depth bound, and Swift control-loss key retirement; the Swift mutation caused event send to succeed and one peer byte to appear. All disposable mirrors were deleted and never changed candidate source.
- Full final baseline after all source fixes: Python `py_compile`; strict Python discovery **96/96 passed twice** under ResourceWarning and DeprecationWarning error gates with tracemalloc and zero targeted warning/unclosed markers (only pre-existing urllib3 LibreSSL NotOpenSSLWarning); focused `LiveHarnessTests` **46/46**; full Swift debug **245/245**; full Swift release **245/245**; release `TarsCompanionApp` product build passed without launch; backend **361/361**; fresh `.env*`/`AGENTS.md`/`CLAUDE.md`/`node_modules`/`.next`-excluded frontend mirror **64/64**, TypeScript noEmit passed, Next production build passed; Swift frontend parse all 10 changed Swift source/tests, bash -n, ShellCheck, plist and entitlements plutil lint, and git diff --check passed.
- Honest discarded attempts: initial focused Python invocation did not execute because of protected Python cache/module path and was rerun correctly; first repaired focused pass exposed three fixture/FAIL-render defects and was not counted; first full Python pass exposed one `safe_blocked` NameError (84 errors) and was not counted; one static attempt used an obsolete plist path and was not counted. Luna repaired only the bounded defects; all final results above are post-repair fresh executions.
- Evidence ceiling remains source/offline only: no app launch, live audio/TCC/device/process enumeration or control, live Security.framework/codesign, signing/Keychain, provider/network/cloud/deploy/production/performance qualification.
- Final post-report digest and fresh exact-digest Sol/Terra review remain pending; no live qualification is claimed.

## Parent independent Final23 verification

- Binding unchanged: branch `codex/task11-live-process-tap-source`; starting HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`; zero staged; exactly 16 intended paths; frozen brief SHA-256 `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Retired Final22 reviewer-bound diff SHA-256: `e52b254262c66e8f2cd8ec71b4529bba45d78c3eccf1c0f2a95cdae7ce66c980`. Root independently reproduced a P2 before review completion: custom `Mapping` iterator/len/getitem/items raising ordinary RuntimeError escaped diagnostic ingress instead of fixed safe FAIL. The in-progress exact-digest reviewers were interrupted; no verdict was reused. The interrupted Sol adversarial child independently found the same P2 in the retired digest.
- Pre-report Final23 canonical `git diff --full-index HEAD --` SHA-256: `7c3d8dc9167e5c60cb5452a4a456b61ca9decd6cf860d905b8db8af1974354f4`.
- Repair: all untrusted diagnostic walkers/fallback iteration now catch ordinary Exception from hostile container protocols, not BaseException control signals; discard partial output, mark ownership failed, emit fixed marker/minimal canonical FAIL, never repr/stringify/retain hostile exception or descendants. New causal test covers diagnostic ingress, typed-fact `_contains_diagnostic`, direct evidence projection/fallback, safe Markdown, no positive claim/nonzero result.
- Root focused 3/3 passed. Disposable mutation restoring the narrow catch failed at hostile items RuntimeError; mirror deleted, candidate untouched.
- Full post-repair matrix: strict Python **97/97 twice** under ResourceWarning/DeprecationWarning gates with tracemalloc and zero targeted warning/unclosed markers (only pre-existing urllib3 LibreSSL NotOpenSSLWarning); focused `LiveHarnessTests` **46/46**; Swift debug **245/245**; Swift release **245/245**; release `TarsCompanionApp` build passed without launch; backend **361/361**; fresh `.env*`/`AGENTS.md`/`CLAUDE.md`/`node_modules`/`.next`-excluded frontend **64/64**, TypeScript noEmit and Next production build passed with telemetry disabled; Python `py_compile`, Swift parse all 10 changed sources/tests, bash -n, ShellCheck, plist/entitlements lint, and git diff check passed.
- Honest discarded attempts: initial focused Python invocation did not execute because of protected Python cache/module path and was rerun correctly; first repaired focused pass exposed three fixture/FAIL-render defects and was not counted; first full Python pass exposed one `safe_blocked` NameError (84 errors) and was not counted; one static attempt used an obsolete plist path and was not counted. Luna repaired only the bounded defects; all final results above are post-repair fresh executions.
- Evidence ceiling remains source/offline only: no app launch, live audio/TCC/device/process enumeration or control, live Security.framework/codesign, signing/Keychain, provider/network/cloud/deploy/production/performance qualification.
- Final post-report digest and fresh exact-digest Sol/Terra reviews remain pending; no live qualification is claimed.
## Parent independent Final24 verification

Binding unchanged: branch `codex/task11-live-process-tap-source`; starting HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`; zero staged; exactly 16 intended paths; frozen brief SHA-256 `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.

Retired Final23 reviewer-bound digest: `e2d30247fc5e20bfd4ef41e27cbcc70cb747cb2e4cbecb68ee72a78a22527405`. Fresh Terra was BLOCKED because P1 shutdown could preserve a proof snapshot from process completion before authenticated acknowledgement, and P2 failed `sendall` retained state credentials. Fresh Sol was BLOCKED because harness-only 43-character key validation leaked into normal controller mode and rejected legacy normal `key-1`. No Final23 approval was reused.

Pre-report Final24 current canonical `git diff --full-index HEAD --` SHA-256: `60864f480ed759aa822887d73f001e2490a90b1d06c75fb5398a71cb8aaaac95`. Report pre-edit SHA-256: `1ff44bc6655828c3699b27467ebebefa13bb389da21e1cb86b6312f9827a901f`.

Repairs: strict canonical nonce-bound keyless shutdown request/ack; ack must be the sole terminal frame with valid decoder EOF; snapshot preservation requires exact ack, never process completion; successful ack immediately revokes state key/session before signal; EOF before ack prevents positive proof; send failures revoke state and run credentials even for `BaseException` while preserving the original exception; Swift coordinator stops the controller, joins the event writer, sends the direct ack, then closes/terminates; normal mode preserves legacy `NativeStreamHandshake`/`key-1` while strict gateway/key grammar is harness-only.

Causal verification: focused Python 4/4; focused Swift `LiveHarnessTests` 51/51; `CompanionSessionControllerTests` 19/19. Disposable combined mutation removing the missing-ack invalidation and restoring process-completion EOF preservation made the exact EOF-before-ack schedule fail on a retained `LiveProofSnapshot`; the mirror was deleted and the candidate was untouched. Mutating only the EOF classifier did not fail because the earlier missing-ack invalidation was redundant protection; it was discarded and not counted.

Full post-repair matrix: strict Python 104/104 twice under `ResourceWarning` and `DeprecationWarning` error gates with `tracemalloc`; full Swift debug 252/252; full Swift release 252/252; release `TarsCompanionApp` product build passed without launch; backend 361/361; fresh excluded frontend 64/64; TypeScript noEmit; Next production build with telemetry disabled; Python `py_compile`; Swift parse all 10 changed files; `bash -n`; ShellCheck; plist/entitlements lint; `git diff --check`.

Honest discarded TypeScript attempt: one malformed `npm --prefix ... exec` invocation printed compiler help and was not counted; the corrected fresh-mirror invocation passed.

Evidence ceiling: source/offline only. No app launch, live audio, TCC/device/process enumeration or control, live Security.framework/codesign, signing/Keychain, provider/network/cloud/deploy/production/performance qualification. Final post-report digest and fresh exact-digest Sol/Terra reviews are pending; there is no live qualification.

## Parent independent Final25 verification

Binding: branch `codex/task11-live-process-tap-source`; starting HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`; zero staged; 16 intended paths; brief SHA `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.

Retired Final24 exact review digest `215e0c6e019a90545528fd97ebad6ab71d0edae78cc975880d2f1b4660a58dcb`; report SHA `25e077b9ac72d45a469e43d139302387aca07084e24a72ef15bf1cf5512e0fd9`. Terra was BLOCKED on P1 post-bind BaseException stranded credentials/control ownership, P1 valid real-recv fragmented shutdown rejection, and P1 unbounded terminal ack. Sol independently BLOCKED the unbounded ack P1 and P2 successful ack retaining core credentials/allowing later events. No approval was reused.

Pre-report Final25 canonical diff SHA: `d3b4dfafdc6b82595fab7f41eaefe021f7fb6739502ded141199b5cb45530909`.

Repairs: complete post-bind BaseException retirement plus phase cleanup before rethrowing non-Exception control signals; Python ack immediately terminalizes/revokes state and blocks later command/event/ack; Swift zero-payload decode continues fragmentation; ack uses one configured finite absolute write deadline with no default and retires authority on failure. Normal `key-1` compatibility is preserved.

Causal tests: Python focused 7/7, including real accepted AF_UNIX post-bind attestor `KeyboardInterrupt` with zero command bytes, cleared run/state credentials, false artifact fact, cleaned owner, and original signal propagation; direct post-ack credential/reuse rejection; sole-terminal ack; EOF-before-ack and ack-before-EOF schedules. Swift focused 53/53, including a waiter blocked before first fragment/remainder and a saturated non-draining peer ack timeout/retirement.

Honest repair iterations: the initial Python focused replay exposed the missing post-ack command guard; the first F1 fixture stopped at fake PID mismatch instead of reaching the attestor; both were repaired and not counted. A parallel capture wrapper reused zsh read-only variable `status`; those Python/frontend/build attempts were discarded and rerun fresh. Disposable mutation copies were created under `/private/tmp`, but the required `apply_patch` tool rejected out-of-project mutation before any edit; copies were deleted, so no new deletion-mutation result is claimed. The original reviewers/root causally reproduced the retired candidate defects.

Full final matrix: strict Python 106/106 twice under `ResourceWarning`/`DeprecationWarning` error gates with `tracemalloc`; Swift focused `LiveHarnessTests` 53/53; Swift debug 254/254; Swift release 254/254; release `TarsCompanionApp` product build passed without launch; backend 361/361; fresh excluded frontend 64/64; TypeScript noEmit; Next production build with telemetry disabled; Python `py_compile`; Swift parse 10 files; `bash -n`; ShellCheck; plist/entitlements lint; `git diff --check`.

Evidence ceiling: source/offline only. No app launch, live audio/TCC/device/process enumeration/control, live Security.framework/codesign, signing/Keychain, provider/network/cloud/deploy/production/performance qualification. Final post-report digest and fresh exact-digest Sol/Terra reviews are pending; no live qualification.
## Parent independent Final26 verification

This section records the parent independent Final26 verification. It is
truthful evidence only and does not claim qualification yet.

### Bound tree

- Pre-report full-index diff SHA: `c7a871a602a72148022ec33acc9cfe5cb94aba09906a554d99b0701290c9d98d`
- Branch: `codex/task11-live-process-tap-source`
- Base HEAD: `5ea4e703cf6c4d6beb958b0946539d3127ff5066`
- Exact changed paths: 16; staged paths: 0.
- Brief SHA: `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`

### Fresh fixes

- Queued event writes retain the active stream key until the shutdown acknowledgement.
- Accepted pre-admission sockets are closed for `BaseException` paths.
- `phase_companion` has durable `cleanup_run` ownership for a post-bind
  `KeyboardInterrupt`, including the main-finalizer retry path.
- The default distribution path has the exact legacy runtime check restored.
- The strict CodeDirectory parser is confined to `signed-app-only`.

### Independent verification

- Focused causal Python: 5/5 passed.
- Strict Python: 111/111 passed twice under `ResourceWarning` and
  `DeprecationWarning` error gates with `tracemalloc`.
- Backend: 361/361 passed.
- Fresh frontend mirror excluding `.env*`, `AGENTS.md`, `CLAUDE.md`,
  `node_modules`, and `.next`: 64/64 passed; TypeScript `noEmit` passed; Next
  production build passed with telemetry disabled.
- `py_compile`, `bash -n`, ShellCheck, plist/entitlements `plutil` lint, and
  `git diff --check` all passed.
- The only warning was the pre-existing urllib3 LibreSSL/NotOpenSSLWarning.

Swift qualification was recovered with a clean environment that isolated HOME,
TMPDIR, CLANG_MODULE_CACHE_PATH, and SWIFTPM_MODULECACHE_OVERRIDE in a fresh
/private/tmp repository-shaped mirror. The new queued-event schedule passed
1/1; focused LiveHarnessTests passed 54/54; full Swift debug passed 255/255;
full Swift release passed 255/255; and the release TarsCompanionApp product
build passed without launching the app.

Discarded attempts: the first focused Python invocation used an unwritable
configured `TMPDIR` and failed before lifecycle fixtures; the corrected
`/private/tmp` replay passed. The first cleanup command pattern was
policy-rejected before execution. Direct Swift and a fresh mirror using the
inherited environment returned permissionDenied before compilation and were
not counted. Clean-environment compilation then exposed two builder fixture
defects in sequence: missing `beforeShutdownSyscall`, then a Swift 6 Sendable
mutable capture; both were repaired and their failing runs were not counted.
The first 54-test run in a flattened package-only mirror produced seven
source-location failures because repository-relative test files were absent;
it was discarded, then the correctly repository-shaped mirror passed 54/54.
No candidate mutation resulted.

### Retrospective starting-HEAD RED reproduction

These commands were run read-only against immutable starting HEAD after
implementation began, not chronologically before implementation. They are
retrospective RED reproductions, not pre-edit observations.

1. `git show 5ea4e703cf6c4d6beb958b0946539d3127ff5066:scripts/verify_live_system_audio.py | nl -ba | sed -n '344,364p;393,441p;981,989p;1088,1089p'` — exit 0. The base showed CompanionRun subprocess argv including `--stream-key` and `--sources system_audio`; `wait_for_capture` accepted `System audio capture active`; the phase recorded ScreenCaptureKit PASS; and argparse `main` was present.
2. `git show 5ea4e703cf6c4d6beb958b0946539d3127ff5066:scripts/verify_live_system_audio.py | sed -n '354,366p'` — exit 0. Exact lines 359–363 showed the companion binary, session-id, `--stream-key`, sources, and gateway.
3. `git show 5ea4e703cf6c4d6beb958b0946539d3127ff5066:companion/native-macos/Sources/TarsCompanionApp/AppDelegate.swift | nl -ba | sed -n '41,44p'` — exit 0. The base showed raw `NSLog("TarsCompanion: URL recebida: %@", urlString)` before parsing.
4. `git cat-file -e` for base `LiveHarnessProtocol.swift`, `LiveHarnessControl.swift`, and `scripts/live_system_audio_harness.py` returned 128 each. Base grep for `AF_UNIX|SO_PEERCRED|audit...|socketpair|activeStreamKey|streamNonce|readFrame|writeFrame|frame_header|frameHeader` returned 1.
5. Base `CompanionSessionController` lines 135–187 — exit 0. The base showed `newSink.start()` at line 160 before source factory lines 178–186, with no actual-source validation.
6. Base controller lines 144–161, 193–205 plus verifier lines 598–614 — exit 0. The base showed callback `update.generation >= activeGeneration` and restart `CompanionRun(session_id, stream_key, "restart")` with the same key, lacking a strict current tuple.
7. Base release-script awk located notary preflight at line 28 and `signed_app_only_line=ABSENT` — exit 0.

### Ceiling and pending gate

This remains source/offline evidence only. No provider, device, production,
live-audio, or deployment claim is made. Prior Final25 review/qualification is
retired. The fresh Final26 source/offline matrix is complete. Final26 remains
pending a fresh canonical full-index diff digest, report hash, and fresh
exact-digest Sol/Terra review. No live qualification is claimed.

## Parent independent Final27 verification

### Bound tree

- Branch: `codex/task11-live-process-tap-source`; base HEAD: `5ea4e703cf6c4d6beb958b0946539d3127ff5066`.
- Zero staged paths and exactly 16 intended changed paths.
- Frozen brief SHA-256: `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Pre-report SHA-256: `7cbb912bb4393ee6f74b010bc62b0bd7cccb37b477834d1377cfb9d354ae388c`.
- Pre-report canonical full-index binary diff SHA-256: `b5cc2e3d984b45d85d4258101c128c433a7ca7ac1d4a3021843f04d115678672`.

### Retired Final26 exact-digest review

The retired Final26 reviewer-bound diff SHA-256 was `29a196e8f18d87ad37dfc446ed0b1171a43b7015b34cfab8e2dfbc6fce2d6923`, with report SHA-256 `7cbb912bb4393ee6f74b010bc62b0bd7cccb37b477834d1377cfb9d354ae388c`. Terra approved that tree, but Sol blocked it on four independently actionable findings: a post-listener/post-launch `BaseException` during `CompanionRun` construction could strand owner resources; delayed duplicate or trailing Swift control input could receive an acknowledgement because no reader retained ownership after request decode; microphone worker join success was not checked before PASS and credential retirement; and a backend process returned before publication could escape cleanup on a control signal. No Final26 approval is reused.

### Final27 repairs

- Python `send_shutdown_request` now sends the canonical frame and closes its write half; any send, shutdown, acknowledgement, or EOF failure retires authority and fails closed.
- Swift retains control-read ownership through exact peer write EOF under one absolute deadline after the request is decoded and bound. Delayed bytes, duplicate frames, partial frames, missing EOF, and timeout all reject without acknowledgement. A credential-free hook pins the exact delayed-input schedules.
- `CompanionRun` construction now transactionally owns and cleans the local server, run directory, returned pre-publication process, helper process, and wait-after-kill path across ordinary exceptions and `BaseException`, while preserving the original control signal.
- Microphone stop now reports whether the worker actually retired, keeps the key only while a worker remains live, and makes PASS require successful stop, no live worker, and no worker error.
- Backend process/log publication and finalization are transactional and retry cleanup in the main finalizer.
- New causal fixtures cover all four retired findings. The normal/default release tail remains the legacy runtime check; the strict parser remains confined to `signed-app-only`.

### Independent verification

- Focused Final27 Python causal tests: 14/14 passed.
- Focused Swift tests: 56/56 passed.
- Strict Python discovery: 125/125 passed twice under `ResourceWarning` and `DeprecationWarning` error gates with `tracemalloc`; only the pre-existing urllib3 LibreSSL `NotOpenSSLWarning` appeared.
- Backend: 361/361 passed.
- Full Swift debug: 257/257 passed.
- Full Swift release: 257/257 passed.
- Release `TarsCompanionApp` product build passed without launching the app.
- Fresh frontend mirrors excluding `.env*`, `AGENTS.md`, `CLAUDE.md`, `node_modules`, and `.next`: unit tests 64/64 passed; TypeScript `noEmit` passed; Next production build passed with telemetry disabled.
- Python compile, `bash -n`, ShellCheck, plist and entitlements `plutil` lint, and `git diff --check` passed.

### Honest discarded attempts

The initial Final27 high builder pass could not execute tests in its agent sandbox; parent verification then found seven Python fixture errors and Swift 55/56. The high retry left three Python fixture failures and the same Swift 55/56 fixture expectation. Flash Medium repaired only the pinned Python fixtures, after which 14/14 passed. One local wrapper for the first Flash Low dispatch had a JavaScript quoting error before `agy` started, used zero builder tokens, and changed nothing; the corrected Flash Low test-only repair produced Swift 56/56. A Python compile attempt targeted an unwritable cache, one malformed unittest selector produced import errors without a valid run, one TypeScript npm wrapper invocation printed compiler help, one static node used obsolete plist paths, and one three-node Swift setup used a sandbox-unwritable configured scratch root. Each was discarded and rerun correctly. Two earlier full Swift runs stopped at 55/56 on the over-strong EOF timing fixture and were not counted. The final results above are fresh post-repair executions.

### Ceiling and pending gate

Evidence remains source/offline only. No app launch, live audio, TCC/device/process enumeration or control, live Security.framework/codesign execution, signing or Keychain action, provider/network/cloud/deployment/production/performance qualification, or real-user/data action occurred. Final27 still requires a fresh post-report digest, report hash, and fresh exact-digest Sol/Terra review. No live qualification is claimed.

## Parent independent Final28 verification

### Bound tree before this report append

- Branch: `codex/task11-live-process-tap-source`.
- Base HEAD: `5ea4e703cf6c4d6beb958b0946539d3127ff5066`.
- Tree: `90f2a66e450c5603cfb15b6236d02f913dc386c8`.
- Zero staged paths and exactly 16 intended changed paths.
- Frozen brief SHA-256: `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Pre-report binary diff SHA-256: `b46b309b7bd43a1c40a0f9f581a2a500953a5860243e6552767365555f30c5e0`.
- Pre-edit report SHA-256: `a28766f66437f54896f36eb97f143df89ed946cab6e4fae691114c4f18ed3888`.

### Final28 repair summary

- Python launcher/spawner publication is callback-first and keyword-only: helper/process ownership is published before any subsequent peer-identity or caller-side operation can raise; returning a different process or returning without callback fails closed and cleans all known processes.
- Companion, backend, interviewer-audio, and restart-replacement caller ownership slots are populated early enough that BaseException/control-signal schedules retain exact cleanup ownership; cleanup failures do not mask the original control signal.
- Completion after control EOF always passes an explicit 5-second timeout and timeout fails cleanly.
- Swift harness stream-key lifetime is tested and hardened for engine mismatch, source-start failure, cancellation versus a newer attempt, runtime terminal failure, and successful capture through stop; cancellation must not retire a newer attempt's key.
- Final tests include real callback/order and opcode/schedule assertions, not source-text-only or false-positive fixtures.

### Honest builder/audit history and telemetry

- Initial gemini-3.7-flash-high conversation `420186a1-39de-4075-a2ee-8eb75136f408`: duration 303.118191s; input 720978; output 80366; thinking 42508; cache 12521895; total 801344. Parent audit found unsafe fallback, missing opcode tests, two Python fixture errors, and a false-positive Swift schedule.
- High retry conversation `b8c3f612-4a67-4fd4-8d08-d9fe635916fe`: duration 542.657138s; input 898923; output 162097; thinking 52148; cache 16578094; total 1061020. Parent audit still found seven concrete test/NameError issues.
- High retry conversation `a35642fc-8c06-4d4d-a107-5ef33da8f761`: duration 136.215663s; input 244649; output 46533; thinking 24308; cache 2953501; total 291182. Parent audit then found one stale facade assertion and one ResourceWarning fixture.
- Medium bounded fixture retry conversation `0e00b461-b3e0-47c0-82be-027c7c42016a`: duration 47.072509s; input 131143; output 12906; thinking 7166; cache 962410; total 144049. It repaired exactly those two tests; parent verification then passed.
- `agy` could edit the authorized worktree but could not persist its private `~/.gemini` conversation/crash state and could not execute its internal terminal checks because of sandbox permissions. Those builder test claims were not relied on.
- Mechanical report append used `gemini-3.7-flash-medium`, conversation `6dd6e431-df9e-40a8-b490-9df992966d91`: duration 16.250392s; input 75639; output 6856; thinking 4369; cache 126615; total 82495. Parent audit found no content or scope defect before this telemetry-only follow-up.

### Fresh parent/independent local verification

- Focused Final28 Python causal tests 15/15 passed with ResourceWarning fatal.
- Full Task11 Python harness discovery passed 140/140 three times: once in 55.012s, once in 76.401s with ResourceWarning and DeprecationWarning fatal plus tracemalloc, and independently in 76.839s under the same strict warning/tracemalloc gate. Deliberate negative-fixture FAIL/SIGKILL text was expected, not a unittest failure.
- Focused Swift exact retirement schedules 5/5 passed, including actual start/stop/newer-attempt ordering.
- Fresh isolated Swift debug 262/262 passed and release 262/262 passed; LiveHarnessTests 61/61 in each configuration. No compiler/test warnings.
- Fresh `.env*`/`AGENTS.md`/`CLAUDE.md`/`node_modules`/`.next`-excluded frontend mirror: 64/64 tests passed; TypeScript `noEmit` passed; Next 16.3.0 production build passed. The only output warning was Node MODULE_TYPELESS_PACKAGE_JSON reparsing of test modules, a performance warning.
- Python `py_compile` for three changed Python paths, `swiftc` frontend parse for all ten changed Swift source/test paths, `bash -n`, ShellCheck, `plutil` lint for `Info.plist` and entitlements, and `git diff --check` passed.
- Backend 361/361 is inherited from Final27, not a fresh Final28 replay. Final28 changed no backend paths. A broad backend rerun was deliberately not performed because backend readiness tests open `.env.example` and the standing authority forbids `.env*` inspection.
- One discarded Swift attempt omitted `--disable-sandbox` and was blocked before compilation by nested SwiftPM sandbox-exec. Fresh reruns disabled only SwiftPM's nested sandbox while retaining the outer Codex sandbox and passed.

### Emergency GitHub Actions gate

- The user imposed an account-wide GitHub Actions cost gate before this report append. No commit, push, PR open/update, Actions rerun/cancel/retry/dispatch, merge, or deployment occurred after that instruction.
- Task11 remains local and dirty by design. Remote GitHub has no Task11 branch ref and no Task11 PR.
- Final28 now awaits the post-report binary diff digest, final report hash, and fresh exact-digest Sol/Terra source review. Do not claim qualification in this append.

### Evidence ceiling

- Source/offline only. No app launch, live audio, TCC/device/process enumeration or control, live Security.framework/codesign execution, signing/Keychain, provider/network/cloud/deployment/production/performance qualification, secrets, real user data, or real-user action occurred.
- No live qualification is claimed.

## Parent independent Final29 verification

### Bound retired Final28 review

- Retired exact diff SHA-256 `881f671c9d1ea1c6581a622156c37dc71deb0639ef8ba4879ae399db86f5cb75`; report SHA-256 `50e030450597ff933d18524f6836ec41a192b78c6f2b6f67c9fa4a65f451317b` at review time.
- Terra APPROVED with no findings, but Sol BLOCKED with two P1 and one P2; no approval was reused.
- P1: `phase_backend` direct `Popen` had a live-child window before local/cleanup-slot publication; Sol reproduced `KeyboardInterrupt` at post-CALL `STORE_FAST` with zero cleanup.
- P1: canceled non-cooperative Swift source start could resume through activation and retain key because the post-await guard omitted `Task.isCancelled`; the cancellation-named test did not call `task.cancel()`.
- P2: `CompanionRun` `mkdtemp`/`chmod`/server setup preceded its cleanup transaction; `chmod` `KeyboardInterrupt` left the private run directory.

### Bound Final29 pre-report tree

- Branch `codex/task11-live-process-tap-source`; base HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`; tree `90f2a66e450c5603cfb15b6236d02f913dc386c8`.
- Exactly 16 changed paths, zero staged, zero untracked.
- Frozen brief SHA-256 `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Pre-report binary diff SHA-256 `28b665e5677625588292f21e92f859e7839e175ac736a2487587ce7f7db73161`.
- Pre-edit report SHA-256 `50e030450597ff933d18524f6836ec41a192b78c6f2b6f67c9fa4a65f451317b`.

### Final29 repairs

- Backend production spawn boundary blocks SIGINT/SIGTERM before `Popen`, publishes the exact process through a required keyword-only callback while blocked, restores the exact prior mask before continuing, fails closed on block/restore failure, rejects missing/different returned ownership, and has one deduplicating `BaseException` cleanup owner for process/log.
- `CompanionRun` cleanup transaction now begins before scratch/temp-parent setup, `mkdtemp`, `chmod`, server construction/bind, or launch; constructor failures clean any run directory/socket/server/process/reader and preserve the original signal/error.
- Swift continuation immediately after `source.start` rejects `Task.isCancelled`. A canceled current owner removes observer and owned fields, stops its source/sink once, returns idle/non-granting, and retires only its key. A stale canceled attempt cannot mutate a newer attempt.
- Causal tests cover spawner callback-then-interrupt, missing callback, published-A/returned-B, signal block/callback/unmask order, restore failure cleanup, real `CompanionRun` `chmod` `KeyboardInterrupt`/`OSError`, actual suspended-task cancellation without stop, and canceled stale completion after a newer successful attempt.

### Builder/audit telemetry

- Initial gemini-3.7-flash-high conversation `80a1d6a8-1ca3-4756-afc1-fdfb6c3f5e79`: duration 266.543947s; input 335690; output 66747; thinking 45679; cache 6106282; total 402437. Parent audit found double cleanup in two invalid-spawner branches and an invalid Swift `event.health` test field.
- Python high retry conversation `672c8e00-feb8-4590-b883-72f6869bd65b`: duration 78.360964s; input 144319; output 26255; thinking 20775; cache 1110348; total 170574. Parent focused Python then passed 8/8.
- Swift mechanical low retry conversation `97af644c-86b0-4456-90de-f03bb818c101`: duration 10.0926s; input 52170; output 1095; thinking 0; cache 142803; total 53265. It fixed only the typed status predicate; parent then found one over-strong pre-cancel state assertion.
- Swift mechanical low retry conversation `e298720f-2b0e-4ffd-8022-142e33345adc`: duration 10.806534s; input 58110; output 2130; thinking 1178; cache 179525; total 60240. It removed only that precondition; parent focused Swift passed 2/2.
- `agy` ran no tests/commands/Git. Parent did not rely on builder test claims.

### Honest discarded attempts

- First focused Python run executed 7 tests and failed 2 once-only assertions because no-callback and A/B-mismatch paths cleaned twice; after retry, 8/8 passed.
- One initial Python wrapper used the sandbox-unwritable configured scratch TMPDIR; `py_compile` setup and cleanup wrapper failed, but the 7 tests still ran and exposed the two real failures. It was not counted as a green verification. Corrected runs used explicit fresh `/private/tmp` roots.
- First Swift compile stopped on invalid test-only `event.health` access; no tests ran.
- Second Swift run compiled and ran two tests: the newer-attempt schedule passed, while the isolated cancel test stopped on an over-strong pre-cancel `.connecting` expectation because the fake transport had already connected. The assertion was removed without production change; the fresh 2/2 replay passed.
- One Swift debug agent wrapper was malformed and rejected before execution; its subsequent fresh run passed and is the counted run.

### Fresh Final29 local verification

- Focused Python causal repair suite 8/8 passed with `ResourceWarning` and `DeprecationWarning` fatal plus `tracemalloc`.
- Full Task11 Python harness discovery 148/148 passed in 68.682s under `ResourceWarning` and `DeprecationWarning` fatal plus `tracemalloc`. Fixture FAIL/SIGKILL text is deliberate negative-case output, not unittest failure. The wrapper's post-test shell assignment used zsh readonly variable status and returned 1 after unittest had printed OK; only the unittest result is counted.
- Focused Swift cancellation schedules 2/2 passed after a clean compile.
- Fresh isolated Swift debug 263/263 passed; LiveHarnessTests 62/62; no warnings.
- Fresh isolated Swift release 263/263 passed; no warnings; app and CLI products compiled but were not launched.
- Python `py_compile` passed for the three changed Python paths; `swiftc` frontend parse passed for all ten changed Swift source/test paths; `bash -n` passed; `plutil` lint passed for `TarsCompanionApp-Info.plist` and `TarsCompanionApp.entitlements`; `git diff --check` passed.
- ShellCheck was unavailable in the final node. Its prior Final28 pass is inherited because the shell script did not change in Final29; do not call it a fresh Final29 run.
- Fresh frontend mirror 64/64, TypeScript `noEmit`, and Next production build are inherited from the same current worktree before Final29; Final29 changed no frontend path.
- Backend 361/361 remains inherited from Final27; Final29 changed no backend path and broad rerun remains excluded because readiness tests open `.env.example` under the no-`.env` authority.

### GitHub Actions emergency gate and pending review

- No commit, push, PR open/update, Actions rerun/cancel/retry/dispatch, merge, deployment, or budget mutation occurred. Task11 remains local and intentionally dirty.
- Final29 awaits a post-report binary diff digest, final report hash, and fresh exact-digest Sol/Terra review. Do not claim final qualification in this section.

### Evidence ceiling

- Source/offline only: no app launch, live audio, TCC/device/process enumeration or control, live Security.framework/codesign, signing/Keychain, provider/network/cloud/deployment/production/performance qualification, secrets, personal data, or real-user action.
- No live qualification is claimed.

## Parent independent Final30 verification

### Bound retired Final29 review

- Retired exact 16-path binary diff SHA-256 `11586e951086b93e369ddba8b3b3d89181d95f33fd6062b822e60dfd07e436d4`; report SHA-256 `e4b13d20deeb1b7a2769b46132832df71d5e59c1d728382bcd8200c8b1996ffd` at review time.
- Terra APPROVED that digest with no P0/P1.
- Sol BLOCKED with one P1 and two P2; no approval was reused.
- P1: `_production_spawn_helper` swallowed `pthread_sigmask` `OSError` and allowed missing `pthread_sigmask`, so `Popen` could run unblocked. A `KeyboardInterrupt` after `Popen` and before `on_helper_spawned` left a live helper with `raw_helper is None`.
- P2: `MacOSLaunchServicesAdapter.launch` identity checks sat between two `try` blocks, so a control signal after spawn return and before `on_process` was not in the cleanup owner.
- P2: canceled `start()` after `await terminalCleanupTask` retired the key but left `.connecting`, so a later `start()` was ignored.

### Bound Final30 pre-report tree

- Branch `codex/task11-live-process-tap-source`; base HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`; tree `90f2a66e450c5603cfb15b6236d02f913dc386c8`.
- Exactly 16 changed paths, zero staged, zero untracked.
- Frozen brief SHA-256 `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Pre-report binary diff SHA-256 `eb9adb5f68b4b379246e882ece463abb1ce4dba39ec34dcbab538afde9a2db9c`.
- Pre-edit report SHA-256 `e4b13d20deeb1b7a2769b46132832df71d5e59c1d728382bcd8200c8b1996ffd`.

### Final30 repairs

- Production helper spawn now matches the backend spawn boundary: required `on_helper_spawned`, fail closed if POSIX `pthread_sigmask` is missing or block raises `OSError`, publish the helper while blocked, restore the exact prior mask, fail closed on restore after publication, and swallow restore errors only on the exception path.
- `launch` identity checks now run inside the same post-spawn cleanup transaction. Protocol mismatch paths still clean once and re-raise; any other `BaseException` after spawn, including a control signal between spawn return and `on_process`, terminates the published helper.
- After `await terminalCleanupTask`, a canceled owner that still owns the attempt idles capture state and retires only its key. A drifted newer attempt still only retires the stale key.
- Causal tests cover helper block/callback/unmask order, missing-or-failed sigmask before `Popen`, restore failure after publication cleaned by `launch`, and canceled second start while prior cleanup is suspended.

### Fresh Final30 local verification

- Focused Python spawn/launch causal suite 6/6 passed with `ResourceWarning` and `DeprecationWarning` fatal plus `tracemalloc`.
- Full Task11 Python harness discovery 151/151 passed in 58.773s under the same warning policy. Fixture FAIL/SIGKILL text is deliberate negative-case output, not unittest failure. Count rose from 148 by the three new helper tests.
- Focused Swift cancel schedules 2/2 passed after a clean compile, including the new post-cleanup-await cancellation test.
- Fresh isolated Swift debug 264/264 passed; LiveHarnessTests 63/63; no warnings.
- Fresh isolated Swift release 264/264 passed; no warnings; app and CLI products compiled but were not launched.
- Python `py_compile` passed for the three changed Python paths; `bash -n` passed; `plutil` lint passed for `Resources/TarsCompanionApp-Info.plist` and `Resources/TarsCompanionApp.entitlements`; `git diff --check` passed.
- ShellCheck was not re-run. The shell script did not change in Final30.
- Frontend and backend suites remain inherited; Final30 changed no frontend or backend path.

### GitHub Actions emergency gate and pending review

- No commit, push, PR open/update, Actions rerun/cancel/retry/dispatch, merge, deployment, or budget mutation occurred. Task11 remains local and intentionally dirty. Meet PRs remain frozen.
- Final30 awaits a post-report binary diff digest, final report hash, and fresh exact-digest Sol/Terra review. Do not claim final qualification in this section.

### Evidence ceiling

- Source/offline only: no app launch, live audio, TCC/device/process enumeration or control, live Security.framework/codesign, signing/Keychain, provider/network/cloud/deployment/production/performance qualification, secrets, personal data, or real-user action.
- No live qualification is claimed.

## Parent independent Final31 verification

### Bound retired Final30 review

- Retired exact 16-path binary diff SHA-256 `e94c63cbc18c8ce349ddb16885711d004768fba2a23c8ba82afce49d203e4cea`; report SHA-256 `e6a5fbf58c988ff1bb1570f2ddddab361e2294eee789b2cef7b6e566606bca09` at review time.
- Terra APPROVED that digest with no P0–P3.
- Sol BLOCKED with one remaining P2; no approval was reused. Final30 helper-sigmask P1 and canceled-start idle P2 were accepted as closed.
- P2: `_production_spawn_helper` restores the signal mask before returning. `launch` then assigned `identity_already_cleaned` and entered a second `try`. A `KeyboardInterrupt` in that gap left a live helper in `raw_helper` only; `on_process` was not called, so `CompanionRun` did not clean.

### Bound Final31 pre-report tree

- Branch `codex/task11-live-process-tap-source`; base HEAD `5ea4e703cf6c4d6beb958b0946539d3127ff5066`; tree `90f2a66e450c5603cfb15b6236d02f913dc386c8`.
- Exactly 16 changed paths, zero staged, zero untracked.
- Frozen brief SHA-256 `8ee8caf9b443cac4d51a4fc14e01e5ce6ebccd26ac2e1450bdea318ef33e4500`.
- Pre-report binary diff SHA-256 `a0ac6283524becd68983bd318b5c57c422b944ff3b5b3899bf6873eb057b21ba`.
- Pre-edit report SHA-256 `e6a5fbf58c988ff1bb1570f2ddddab361e2294eee789b2cef7b6e566606bca09`.

### Final31 repairs

- `MacOSLaunchServicesAdapter.launch` now owns spawn, identity checks, facade construction, `on_process`, and `PeerIdentity` in one `try`. `identity_already_cleaned` is initialized before that `try`. Protocol mismatch paths still clean once. Any other `BaseException` after helper publication, including `LaunchServicesProcess` construction, terminates the published helper before `on_process`.
- Causal test patches `LaunchServicesProcess.__init__` with `KeyboardInterrupt` and asserts the helper is terminated/waited once and `on_process` is never called.

### Fresh Final31 local verification

- Focused Python spawn/launch causal suite 6/6 passed with `ResourceWarning` and `DeprecationWarning` fatal plus `tracemalloc`.
- Full Task11 Python harness discovery 152/152 passed in 58.371s under the same warning policy. Count rose from 151 by the new post-spawn `KeyboardInterrupt` test.
- Swift debug 264/264, Swift release 264/264, and LiveHarnessTests 63/63 are inherited from Final30; Final31 changed no Swift path.
- Python `py_compile` passed for the two changed Python paths; `git diff --check` passed.
- Frontend and backend suites remain inherited; Final31 changed no frontend or backend path.

### GitHub Actions emergency gate and pending review

- No commit, push, PR open/update, Actions rerun/cancel/retry/dispatch, merge, deployment, or budget mutation occurred. Task11 remains local and intentionally dirty. Meet PRs remain frozen.
- Final31 awaits a post-report binary diff digest, final report hash, and fresh exact-digest Sol/Terra review. Do not claim final qualification in this section.

### Evidence ceiling

- Source/offline only: no app launch, live audio, TCC/device/process enumeration or control, live Security.framework/codesign, signing/Keychain, provider/network/cloud/deployment/production/performance qualification, secrets, personal data, or real-user action.
- No live qualification is claimed.
