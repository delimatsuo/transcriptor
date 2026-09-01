import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_MEET_IMPORT_BYTES,
  MeetTranscriptImportError,
  buildMeetTranscriptSyncRequest,
  parseMeetImportFixture,
  parseMeetImportResult,
  parseMeetTranscriptAutomationResult,
  validateMeetImportFile,
} from "./meetTranscriptImport";

const fixture = JSON.stringify({
  sourceType: "GOOGLE_MEET",
  sourceArtifactId: "artifact-1",
  title: "Synthetic interview",
  noticeGiven: true,
  noticeProvenance: "Synthetic fixture",
  transcriptSessions: [{
    name: "transcript-1",
    entries: [{ name: "entry-1", participant: "participant-1", text: "Hello" }],
  }],
});

test("manual fixture accepts only a bounded local JSON object", () => {
  assert.equal(parseMeetImportFixture(fixture, "fixture.json").sourceType, "GOOGLE_MEET");
  assert.throws(
    () => parseMeetImportFixture("[]", "fixture.json"),
    MeetTranscriptImportError,
  );
  assert.throws(
    () => validateMeetImportFile("/tmp/fixture.json", fixture.length),
    /not a path|não um caminho/i,
  );
  assert.throws(() => validateMeetImportFile("fixture.txt", 10), /json/i);
  assert.throws(
    () => validateMeetImportFile("fixture.json", MAX_MEET_IMPORT_BYTES + 1),
    /2 MB/i,
  );
});

test("fixture parser rejects malformed and non-Meet objects", () => {
  assert.throws(() => parseMeetImportFixture("{", "fixture.json"), /JSON válido/i);
  assert.throws(
    () => parseMeetImportFixture(JSON.stringify({ sourceType: "DRIVE" }), "fixture.json"),
    /Meet válido/i,
  );
  assert.throws(
    () => parseMeetImportFixture(
      JSON.stringify({ ...JSON.parse(fixture), filesystemPath: "/tmp/fixture.json" }),
      "fixture.json",
    ),
    /Meet válido/i,
  );
});

test("result parser accepts only content-free import status", () => {
  const valid = {
    session_id: `meet-import-${"1".repeat(32)}`,
    source_key: "2".repeat(64),
    source_digest: "3".repeat(64),
    status: "completed",
    segment_count: 3,
    attempt_count: 1,
    idempotent_replay: false,
  };
  assert.deepEqual(parseMeetImportResult(valid), valid);
  assert.throws(
    () => parseMeetImportResult({ ...valid, participantName: "leak" }),
    /resposta/i,
  );
  assert.throws(
    () => parseMeetImportResult({ ...valid, status: "running" }),
    /resposta/i,
  );
});

test("eligible-event sync request uses only the three exact identity keys", () => {
  const request = buildMeetTranscriptSyncRequest({
    grantId: "grant-1",
    calendarId: "recruiter@example.com",
    calendarEventId: "event-1",
  });
  assert.deepEqual(request, {
    grantId: "grant-1",
    calendarId: "recruiter@example.com",
    calendarEventId: "event-1",
  });
  assert.equal(
    JSON.stringify(request),
    '{"grantId":"grant-1","calendarId":"recruiter@example.com","calendarEventId":"event-1"}',
  );
  assert.throws(
    () => buildMeetTranscriptSyncRequest({ ...request, grantId: " grant-1" }),
    /autorização/i,
  );
  assert.throws(
    () => buildMeetTranscriptSyncRequest({ ...request, calendarId: "calendar id" }),
    /calendário/i,
  );
  assert.throws(
    () => buildMeetTranscriptSyncRequest({ ...request, calendarEventId: "event 1" }),
    /evento/i,
  );
});

test("automation response parser requires the strict import shape and replay bit", () => {
  const valid = {
    session_id: `meet-import-${"1".repeat(32)}`,
    source_key: "2".repeat(64),
    source_digest: "3".repeat(64),
    status: "completed",
    segment_count: 3,
    attempt_count: 1,
    idempotent_replay: false,
    automation_replay: true,
  };
  assert.deepEqual(parseMeetTranscriptAutomationResult(valid), valid);
  assert.throws(
    () => parseMeetTranscriptAutomationResult({ ...valid, transcriptText: "leak" }),
    /sincronização/i,
  );
  const { automation_replay: _replay, ...missingReplay } = valid;
  assert.throws(
    () => parseMeetTranscriptAutomationResult(missingReplay),
    /sincronização/i,
  );
});
