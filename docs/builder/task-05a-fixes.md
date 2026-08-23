# Task 05a verification fixes — preserve a healthy overlapping owner and place the warning below badges

Read `docs/builder/README.md` and `docs/builder/task-05a-brief.md` first. This is a narrow repair pass over your current uncommitted Task 05a implementation. Do not broaden scope.

Modify only the existing Task 05a file plan. You may additionally modify this fix brief only if needed to record an unavoidable clarification; otherwise leave it unchanged. Do not touch the three protected untracked instruction files. Do not run any Git command, including read-only Git commands.

## Finding 1 — a never-producing overlapping owner can clobber a healthy owner

Current behavior is incorrect when two live connections own the same source:

1. Connection A produces a `microphone` frame, so the merged source is `healthy`.
2. While A remains live, connection B sends a valid microphone hello but produces no frame.
3. B's first-frame deadline expires.
4. B currently overwrites the shared microphone state with `device_unavailable`, adds an alert, and logs a never-produced warning even though A still owns and carries the source healthily.

That violates the Task 05a merged-view rule: one connection may not clobber a still-live owner of the same source.

Repair the never-produced branch so that THIS connection's expired first-frame deadline does not alarm or overwrite the merged state when all of the following are true:

- `source_connections[source] > 1`; and
- the merged source state is currently `healthy` (which proves another owner has produced successfully, because this connection has not).

Keep this connection's original `intended_since` timestamp. If the healthy owner later disconnects and this connection becomes the sole owner without ever producing, the next watchdog pass must be allowed to raise the already-expired alarm. Do not reset or extend the deadline. Do not change the existing post-first-frame stall policy in this repair.

Add a focused backend regression test named:

```text
test_never_produced_overlap_does_not_clobber_healthy_owner
```

The test must hold connection A open after it produces a valid microphone frame, run connection B for the same active session, have B announce microphone and wait beyond a monkeypatched never-produced deadline without producing, and inspect state while A is still live. Assert:

- microphone remains `healthy`;
- no payload caused by B contains the never-produced alert message or changes microphone to `device_unavailable`;
- B does not log `native_source_never_produced_frames` for microphone while A remains the healthy owner;
- after B disconnects while A remains live, microphone is still `healthy` and physical capture is still `active`.

Use deterministic millisecond watchdog constants and existing fake WebSocket helpers. Do not use a real sleep longer than needed.

## Finding 2 — warning layout is not below the source badges

`CaptureSourceStatus.tsx` currently puts both badges and the alert in one wrapping row. On a wide viewport the alert appears beside the badges, not below them as required.

Make the structure explicit:

- an outer group arranged vertically;
- an inner wrapping row containing exactly the two source badges;
- the separate amber `role="status"` / `aria-live="polite"` warning after and below that row.

Preserve the badge visuals, exact server message, visible `⚠`, source badges, and accessibility semantics. Do not add dependencies or snapshots.

## Finding 3 — whitespace/report accuracy

Remove the extra blank line at EOF in `backend/tests/test_native_stream_endpoint.py`. The current designer check reports:

```text
backend/tests/test_native_stream_endpoint.py:967: new blank line at EOF.
```

Update `docs/builder/task-05a-report.md` with a short verification-fix section and the real final commands/counts. Do not claim the tree is whitespace-clean unless your non-Git text check actually confirms it.

## Verification

Run and report:

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm test
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm run build
```

Expected minimums after the new regression: endpoint 36, backend 301, frontend 62, all zero failures; Next.js build succeeds.

Do not commit. Stop with the working tree ready for designer verification.
