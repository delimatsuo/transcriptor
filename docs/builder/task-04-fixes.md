# Task 04 — Verification fixes

Ignore `docs/builder/task-04-brief.md`; its implementation round is complete. Your ONLY task now is this fixes file.

Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote the path). Do not run any git command. Do not touch any file except:

- `backend/main.py`
- `backend/tests/test_native_stream_endpoint.py`
- `docs/builder/task-04-report.md`

## Finding 1 — Enforce the exact two-entry subprotocol shape

The contract says the raw comma-separated offer must contain exactly two entries. The implementation currently drops empty entries:

```python
[item.strip() for item in raw.split(",") if item.strip()]
```

That incorrectly accepts malformed three-entry offers such as `tars-stream, <valid-key>,` or `tars-stream, , <valid-key>` after the empty entry is discarded.

Fix the parser so it strips each raw entry but does not remove empty entries before checking `len(offered) == 2`. Preserve all fallback and authentication behavior from the original brief.

Add a regression test named `test_native_stream_rejects_subprotocol_with_extra_empty_entry` using a valid installed key and a header shaped like `tars-stream, <valid-key>,`. With no query parameter it must remain unaccepted and close with code `1008`.

## Finding 2 — Prove the deprecation warning occurs exactly once

`test_native_stream_query_key_still_works_and_warns` currently uses `any(...)`, which would pass if the endpoint emitted the deprecation warning multiple times. Replace that weak assertion with an exact assertion proving the recorder saw one and only one call:

```python
warnings_recorded == [
    ("native_stream_query_key_deprecated", {"session_id": "s-query-warn"})
]
```

Equivalent exact-count syntax is acceptable, but it must also verify the event name and session ID.

## Finding 3 — Remove the whitespace error

`git diff --check` currently reports:

```text
backend/tests/test_native_stream_endpoint.py:756: new blank line at EOF.
```

Make the file end with exactly one newline and no extra blank line.

## Verification

Run both commands and record their real output:

```bash
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests/test_native_stream_endpoint.py -q
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && .venv/bin/python -m pytest backend/tests -q
```

Append a `Verification fixes` section to `docs/builder/task-04-report.md` describing the three repairs and the new totals. Do not rewrite or erase the original RED/GREEN evidence.

## Finding 4 — Clean the report itself

The designer's staged whitespace gate found three report-only errors:

```text
docs/builder/task-04-report.md:63: trailing whitespace.
docs/builder/task-04-report.md:70: trailing whitespace.
docs/builder/task-04-report.md:163: new blank line at EOF.
```

Remove the spaces from the two otherwise blank lines and make the report end with exactly one newline. Do not change the report's prose or evidence. This is a report-only follow-up: do not edit `backend/main.py` or `backend/tests/test_native_stream_endpoint.py` again, and do not rerun their tests.

Do not run any git command, including read-only `git status`, `git diff`, or `git diff --check`. The designer owns every Git inspection and write.
