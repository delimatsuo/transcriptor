import assert from "node:assert/strict";
import test from "node:test";

import {
  formatNoteOffset,
  latestFinalNoteAnchor,
} from "./recruiterNotes.ts";
import type { TranscriptSegment } from "../types/ws.ts";

function segment(
  id: string,
  endTime: number,
  isFinal: boolean,
): TranscriptSegment {
  return {
    id,
    text: "fixture",
    speaker: "Candidato",
    start_time: 0,
    end_time: endTime,
    confidence: 0.9,
    sequence_number: 1,
    is_final: isFinal,
  };
}

test("notes anchor to the latest durable final and ignore newer interim speech", () => {
  assert.deepEqual(
    latestFinalNoteAnchor([
      segment("seg-final-1", 4.2, true),
      segment("seg-interim", 8.9, false),
      segment("seg-final-2", 7.251, true),
      segment("seg-interim-new", 9.1, false),
      segment("seg-final-late-arrival", 6.4, true),
    ]),
    {
      transcriptSegmentId: "seg-final-2",
      transcriptOffsetMs: 7251,
    },
  );
});

test("notes stay disabled without a valid final evidence anchor", () => {
  assert.equal(latestFinalNoteAnchor([]), null);
  assert.equal(latestFinalNoteAnchor([segment("interim", 2, false)]), null);
  assert.equal(latestFinalNoteAnchor([segment("invalid", Number.NaN, true)]), null);
});

test("note offsets use a compact transcript-relative clock", () => {
  assert.equal(formatNoteOffset(0), "00:00");
  assert.equal(formatNoteOffset(72_999), "01:12");
});
