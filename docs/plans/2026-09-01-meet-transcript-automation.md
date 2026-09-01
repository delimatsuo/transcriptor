# Google Meet transcript automation — design only

Date: 2026-09-01
Next stacked branch: `codex/meet-transcript-automation`

## Scope and evidence ceiling

This document defines a future automation slice. It does not authorize provider
access, OAuth consent, credential creation, webhook registration, live event
receipt, deployment, or processing of real meeting data. The current manual
fixture importer remains the only implementation entry point and uses the same
provider-independent durable worker described below.

## Authorization boundary

Google Workspace/Meet OAuth is a separate delegated user authorization from
Firebase application authentication. Firebase establishes the T.A.R.S. owner
and organization; it must never be treated as authority to read Calendar or
Meet resources. The automation must request the least privileged Meet and
Calendar scopes that support the exact eligible-event and transcript reads,
retain explicit grant provenance, and fail closed when either authorization is
missing, expired, revoked, or belongs to a different principal.

No provider token, refresh token, authorization code, webhook secret, or raw
provider payload belongs in the transcript import job or report sources.

## Exact resource discovery

Eligibility is keyed by an explicit Calendar event ID plus the authenticated
calendar/account scope. The system must not infer eligibility from event title,
attendee text, meeting name, Drive folder, or filename.

Subscribe only to the exact
`google.workspace.meet.transcript.v2.fileGenerated` event. Its event payload
carries the exact transcript resource name. Use that exact resource as the
source artifact identity, retrieve that transcript, then list structured
`transcripts.entries` under that exact transcript parent. Do not scan Drive,
folders, filenames, or unrelated conference records.

Pagination must be bounded by explicit maximum pages, entries, response bytes,
wall time, and retry count. A missing page token termination, repeated token,
identity mismatch, malformed entry, unknown field, or any bound breach fails
closed before the provider-independent worker is called.

## Durable event and import flow

1. Authenticate the webhook and persist a content-free event envelope keyed by
   provider subscription identity plus exact event ID.
2. Deduplicate redelivery durably. A process-local set or task registry is not
   sufficient.
3. Bind the event to the exact Firebase owner/organization and explicit Calendar
   event eligibility record. Scope mismatch is a non-enumerating not-found.
4. Resolve the exact transcript resource from `fileGenerated`; retrieve its
   structured entries with bounded pagination.
5. Construct the same strict `GoogleMeetImportRequest` accepted by the manual
   fixture path. Server time supplies `imported_at`.
6. Feed that request into `GoogleMeetImportWorker`. Do not create sessions,
   transcripts, or report work through a second code path.
7. Mark the webhook event completed only after the worker's atomic import commit
   is durably completed. Preserve content-free reason codes for retryable
   failures.

The event record and transcript import job use independent idempotency keys:
event ID prevents webhook redelivery work, while the worker source key prevents
the same exact transcript artifact from producing more than one interview.

## Reconciliation and manual recovery

A bounded reconciler may inspect a finite recent window of explicitly eligible
Calendar event IDs and compare them with durable webhook/event records. It must
have per-run event/page/time/request limits, backoff, a lease, and a portfolio
cost ceiling. It must not scan all historical events or Drive.

The UI should expose a manual sync action for one explicit eligible Calendar
event ID. Manual sync follows the same exact-resource resolution and worker path;
it is not a bypass for OAuth, scope, bounds, tombstones, or digest conflicts.
The existing local JSON fixture import remains the offline fallback when no live
provider authorization is available.

## Required qualification before implementation or rollout

- Synthetic webhook authentication, replay, reordering, duplicate, and forged
  scope tests.
- Exact event-ID eligibility tests proving title heuristics cannot admit work.
- Bounded pagination tests for repeated tokens, excess pages/entries/bytes, and
  partial provider responses.
- OAuth separation and least-privilege review; no Firebase-to-Meet authority
  substitution.
- Mutation-effective proof that every automation path enters the existing
  durable worker and honors digest conflicts and session tombstones.
- Recovery tests for webhook crash points, expired leases, reconciliation, and
  manual sync redelivery.
- Live credentials, provider calls, webhook registration, deployment, and real
  data remain separately owner-gated evidence.
