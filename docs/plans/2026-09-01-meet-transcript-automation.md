# Google Meet transcript automation — synthetic/offline implementation contract

Date: 2026-09-01
Next stacked branch: `codex/meet-transcript-provider`

## Scope and evidence ceiling

This slice implements provider-independent contracts, parsers, durable state,
and synthetic entry points only. It does not authorize provider access, OAuth
consent, credential creation, webhook registration, live event receipt,
deployment, or processing of real meeting data. No production Google adapter or
token verifier is installed by this slice. The existing import worker remains
the only path that may publish an imported interview.

Public contract references used to pin this offline implementation:

- Google Workspace Events overview and Pub/Sub CloudEvent envelope:
  <https://developers.google.com/workspace/events>
- Meet event types and transcript resource payload:
  <https://developers.google.com/workspace/events/guides/events-meet>
- Meet transcript-entry list bounds and page-token contract:
  <https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts.entries/list>
- Google Workspace Events and Meet OAuth scopes:
  <https://developers.google.com/workspace/events/guides/auth>
- Calendar event read scope:
  <https://developers.google.com/workspace/calendar/api/auth>
- Authenticated Pub/Sub push JWT validation:
  <https://cloud.google.com/pubsub/docs/authenticate-push-subscriptions>

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

The durable grant metadata contract contains no credentials. An active grant
must belong to the exact Firebase owner and organization, name the exact
Workspace subject, and include both of these least-privilege read scopes:

- `https://www.googleapis.com/auth/calendar.events.readonly`
- `https://www.googleapis.com/auth/meetings.space.readonly`

The second scope covers subscription creation for the transcript-generated
event and listing structured transcript entries. Firebase authentication never
substitutes for either Workspace scope.

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

An eligible binding is the exact tuple of grant ID, Workspace subject, Calendar
ID, Calendar event ID, Meet target resource, Workspace subscription source, and
Pub/Sub subscription name. It also holds the import title/notice/context needed
to construct the existing strict request. Titles, invitee text, filenames, and
other fuzzy metadata are never selectors.

The push endpoint accepts only a bounded Pub/Sub wrapper with CloudEvent
attributes `ce-datacontenttype`, `ce-id`, `ce-source`, `ce-specversion`,
`ce-subject`, `ce-time`, and `ce-type`. `ce-type` must be exactly
`google.workspace.meet.transcript.v2.fileGenerated`; decoded data must contain
only the exact `conferenceRecords/{record}/transcripts/{transcript}` name.
Before decoding or persisting the event, the injected verifier must validate the
Bearer JWT signature and expiry, expected audience, exact configured push
service-account email, and `email_verified=true`. Tokens and raw envelopes are
never logged or stored.

The offline provider seam returns raw JSON for one exact transcript parent.
Each request uses `pageSize=100`; the orchestrator permits at most four pages,
400 unique entries, 2,000,000 response bytes, 30 seconds, and one request per
page (zero automatic retries). Empty/repeated page tokens, a page containing
more than 100 entries, an entry outside the exact transcript parent, duplicate
entry identity with different content, or a nonterminal token at any bound
fails closed before import publication.

The finite 30-second bound is enforced by the offline orchestrator seam. If
provider work resists cancellation, the orchestrator cancels and detaches that
work without waiting for it to settle, retains it only for exception cleanup,
and caps detached or pending provider tasks at 25 per orchestrator. Reaching
that cap fails closed. This is an offline lifecycle contract only; no live
provider transport is implemented.

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

The event idempotency key is the hash of exact Workspace subscription source and
CloudEvent ID. Its durable record stores only identities, status, version,
attempt count, lease metadata, content-free reason code, and final import
identity. Queue precedes claim; only the current unexpired lease may complete or
fail; a completed record is an idempotent replay; an expired lease is
recoverable; a digest/resource/scope conflict is not.

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

The implemented reconciliation contract processes only stored eligible
bindings, at most 25 per run and for at most 30 seconds, under a durable scoped
lease. A durable canonical binding-key cursor rotates each run past the last
binding actually considered and wraps once, instead of rescanning a fixed
prefix that could starve later eligible bindings. It never lists Calendar or
Drive. The authenticated manual-sync request names the exact grant ID, Calendar
ID, and Calendar event ID; resolution must echo that binding plus its Meet
target before the transcript resource is accepted. Webhook, reconciliation,
and manual sync all call the same automation orchestrator and then the existing
`GoogleMeetImportWorker`.

Synthetic HTTP seams:

- `POST /webhooks/google-workspace/meet-transcripts` authenticates its own
  Pub/Sub push identity and never accepts Firebase authority.
- `POST /api/workspace/meet-transcripts/sync` requires the Firebase principal
  and the exact eligible-event tuple.
- `POST /api/workspace/meet-transcripts/reconcile` requires the Firebase
  principal and a bounded grant-scoped request.

Without injected offline verifier/provider/orchestrator dependencies, all three
seams fail closed with service unavailable. This slice does not initialize a
live adapter from settings or environment.

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
- Route tests proving Pub/Sub authentication is separate from Firebase and that
  a valid synthetic push cannot escape the exact stored subscription/subject.
- Mutation-effective spies proving webhook, reconciliation, and manual sync
  each call the existing import worker exactly once on first success and never
  on malformed, unbound, forged, duplicate-complete, or over-bound input.
- Live credentials, provider calls, webhook registration, deployment, and real
  data remain separately owner-gated evidence.
