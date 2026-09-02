# Google Meet transcript provider adapter — offline HTTP/JWT slice

Date: 2026-09-01
Base: `eb9a1cec4405a697cda9c831021b0dbbe6e477f0`
Stacked branch: `codex/meet-transcript-provider`
Stacked on: `codex/meet-transcript-automation` (draft PR #49)

## Scope and evidence ceiling

This slice implements a live-shaped Calendar/Meet HTTP adapter and a Pub/Sub
push JWT verifier. It still does not authorize Google account access, OAuth
consent, credential files, live API calls, webhook registration, deployment, or
real transcripts. Runtime continues to leave the automation seams unset unless
a test or a later owner-gated installer injects an explicit transport and
token source. Process environment is never a credential source.

Pinned public contracts:

- Calendar events.get:
  <https://developers.google.com/workspace/calendar/api/v3/reference/events/get>
- Meet conferenceRecords.list filter syntax:
  <https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords/list>
- Meet conferenceRecords resource:
  <https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords>
- Meet transcripts.list:
  <https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts/list>
- Meet transcriptEntries.list:
  <https://developers.google.com/workspace/meet/api/reference/rest/v2/conferenceRecords.transcripts.entries/list>
- Pub/Sub authenticated push JWT:
  <https://cloud.google.com/pubsub/docs/authenticate-push-subscriptions>
- Google OAuth2 ID-token certs URL used by google-auth:
  `https://www.googleapis.com/oauth2/v1/certs`

## Adapter contract

Injected dependencies only:

- HTTP transport: one GET, caller-supplied timeout, no retries, no redirects.
- Access-token source: returns a bearer token for the already-authorized grant.
- Clock: used only for JWT `exp`.

The adapter never reads `.env`, `GOOGLE_APPLICATION_CREDENTIALS`, or any other
process-environment secret. `create_meet_transcript_provider_runtime()` returns
`None` unless every dependency is supplied explicitly.

`backend/main.py` lifespan still initializes the push verifier and orchestrator
to `None`. Presence of environment variables must not change that.

### Resolve

One exact Calendar event, then one conference-record list, then one transcript
list. Discovery from titles, attendees, Drive, or extra conference pages is
forbidden.

1. `GET https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events/{eventId}`
   with both path segments percent-encoded. Require HTTP 200, exact `id`,
   `conferenceData.conferenceSolution.key.type == "hangoutsMeet"`, and a
   meeting-code `conferenceId` matching `^[A-Za-z0-9_-]{1,512}$`.
2. `GET https://meet.googleapis.com/v2/conferenceRecords?filter={filter}&pageSize=100`
   where `{filter}` is exactly `space.meeting_code = "{conferenceId}"`.
   Require HTTP 200, exactly one `conferenceRecords[]` row, no `nextPageToken`,
   and `space` equal to the binding target with the `//meet.googleapis.com/`
   prefix removed (`spaces/{id}`).
3. `GET https://meet.googleapis.com/v2/{conferenceRecord}/transcripts?pageSize=100`.
   Require HTTP 200, exactly one transcript with `state == "FILE_GENERATED"`,
   no `nextPageToken`, and `name` matching the existing transcript resource
   pattern.

Unknown JSON fields, extra pages, zero/multiple matches, non-200 responses, or
bodies above 2,000,000 bytes fail closed. Calendar 404 is not-found; other
provider failures are invalid. Exception text stays content-free.

### Fetch

`GET https://meet.googleapis.com/v2/{transcript}/entries?pageSize=100` with
optional `pageToken`. Return the raw 200 body bytes. Do not parse entries here;
the existing orchestrator already does. Non-200, empty, or oversized bodies
fail closed. Exactly one request per call.

Every Meet/Calendar GET sends `Authorization: Bearer {token}` and
`Accept: application/json`. Certs GET sends only `Accept: application/json`.
Timeout is 10 seconds per request.

### Push JWT

Verify locally against injected certs fetched from
`https://www.googleapis.com/oauth2/v1/certs` through the same transport:

- `alg` is `RS256`
- `iss` is `https://accounts.google.com`
- signature matches the `kid` certificate
- `exp` is strictly in the future
- `aud`, `email`, and boolean `email_verified` are returned as
  `PushTokenClaims`

Tokens and raw envelopes are never logged or stored. String `"true"` is not
boolean true.

## Qualification

- Exact URL, query, header, and pageSize assertions against a mock HTTP
  transport. No network to `googleapis.com` or `accounts.google.com`.
- Mutation-effective proof that a 500 does not retry.
- JWT accept/reject for audience, email, `email_verified`, issuer, expiry, and
  signature.
- Proof that `main.py` still leaves automation seams unset.
- Live credentials, provider calls, Pub/Sub registration, and deployment remain
  owner-gated.
