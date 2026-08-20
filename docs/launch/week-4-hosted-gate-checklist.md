# Week 4 hosted gate checklist

**Status:** Non-authorizing operator checklist. This document does not approve
hosted access, deployment, migration, deletion, provider calls, or real
interviews.

## Purpose

Use this checklist only after the owner names the exact hosted project, account,
runtime identity, operator, and approved mutation set. Keep the Week 4 source
and test artifact separate from hosted evidence. Any source or Git-index change
invalidates device or hosted evidence until the exact SHA is rebound.

## Bind the exact artifact

Record before any hosted work:

| Field | Required value |
| --- | --- |
| Branch | `codex/week-4-auth` |
| Commit SHA | `git rev-parse HEAD` and remote PR head |
| Pull request | [#8](https://github.com/delimatsuo/transcriptor/pull/8) |
| Owner authorization | Named owner, date, environment, and mutation set |
| Project/account/runtime | Exact project ID, active account, and runtime identity |

Stop if local and remote SHAs differ, the project is not the authorized
environment, or the active identity is not the approved identity. Never paste
access tokens, service-account keys, transcript text, candidate names, or raw
provider payloads into the evidence record.

## Read-only preflight

1. Confirm the active CLI project and account using redacted, read-only output.
   Do not change the project or account as part of this checklist.
2. Run the backend ADC probe before any Firestore call. A failure must stop the
   run with the exact remediation:
   `ADC expirado — rode: gcloud auth application-default login`.
3. Confirm the approved project, billing/quota guard, deployment state, and
   runtime identity from the owner-provided environment record. Do not infer
   them from mutable labels or a local `.env` file.

## Firestore composite index

The required index is the `sessions` collection query on:

1. `ownerId ASCENDING`
2. `orgId ASCENDING`
3. `startedAt DESCENDING`

Read the hosted index state first. Deploy only the checked-in
`firestore.indexes.json`, using the provider’s canonical index-only command,
after the owner has approved that exact mutation and protected approval is
recorded. Do not use a broad deploy command. Verify the index reaches `READY`
and record the project, index definition, command, result, and timestamp.

## Legacy ownership readback

Run the versioned, read-only inventory from the exact artifact:

```bash
.venv/bin/python -m backend.scripts.inventory_legacy_scope
```

The output contains only session IDs and ownership/status metadata. Save a
redacted or access-controlled artifact hash plus the counts needed to reconcile
the historical containment inventory with the owner-authorized purge report.

- Do not delete, quarantine, backfill, or auto-claim records during this step.
- Any record missing `ownerId` or `orgId` remains inaccessible and requires a
  separately approved migration decision.
- If the readback contradicts the historical purge report, stop and escalate;
  do not broaden the query or inspect transcript/document content by default.

## Stop and rollback boundaries

Stop immediately on project/account/runtime mismatch, unexpected public access,
an ADC timeout, an index definition drift, unexpected data, credential output,
or any content-bearing log. Preserve the read-only evidence and notify the
owner. Index rollback or any data mutation requires a new owner-approved
mutation set; this checklist never authorizes destructive rollback.

## Evidence handoff

Attach the exact SHA, owner authorization, redacted identity/project readback,
index before/after state, inventory artifact hash/counts, operator, timestamps,
and explicit PASS/FAIL/NOT RUN results. The handoff must state separately that
source tests, CI, and this checklist do not prove hosted tenant isolation,
provider quota enforcement, deletion completion, or physical audio behavior.
