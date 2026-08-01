import assert from "node:assert/strict";
import { test } from "node:test";

import { mergeTranscriptSegment } from "./transcript.ts";
import type { TranscriptSegment } from "@/types/ws";

let seq = 0;

function seg(
  speaker: string,
  text: string,
  is_final: boolean,
): TranscriptSegment {
  seq += 1;
  return {
    id: `seg-${seq}`,
    text,
    speaker,
    start_time: seq,
    end_time: seq + 1,
    confidence: 0.9,
    sequence_number: seq,
    is_final,
  };
}

const ENTREVISTADOR = "Entrevistador";
const CANDIDATO = "Candidato";

function textsBySpeaker(list: TranscriptSegment[]) {
  return list.map((s) => `${s.speaker}:${s.text}${s.is_final ? "" : "…"}`);
}

test("keeps one interim per speaker when both are mid-sentence", () => {
  // Both capture streams are always live, so during any two-way exchange
  // both speakers have an interim in flight at the same time.
  let t: TranscriptSegment[] = [];
  t = mergeTranscriptSegment(t, seg(ENTREVISTADOR, "qual é a sua", false));
  t = mergeTranscriptSegment(t, seg(CANDIDATO, "bom eu acho", false));

  assert.deepEqual(textsBySpeaker(t), [
    "Entrevistador:qual é a sua…",
    "Candidato:bom eu acho…",
  ]);
});

test("a newer interim replaces only the same speaker's interim", () => {
  let t: TranscriptSegment[] = [];
  t = mergeTranscriptSegment(t, seg(ENTREVISTADOR, "qual é a sua", false));
  t = mergeTranscriptSegment(t, seg(CANDIDATO, "bom eu acho", false));
  t = mergeTranscriptSegment(t, seg(CANDIDATO, "bom eu acho que sim", false));

  assert.deepEqual(textsBySpeaker(t), [
    "Entrevistador:qual é a sua…",
    "Candidato:bom eu acho que sim…",
  ]);
});

test("one speaker finalizing does not erase the other speaker's interim", () => {
  // The reported symptom: the candidate is mid-sentence, the interviewer's
  // utterance finalizes, and the candidate's partial text disappears.
  let t: TranscriptSegment[] = [];
  t = mergeTranscriptSegment(t, seg(CANDIDATO, "eu trabalhei dez anos", false));
  t = mergeTranscriptSegment(t, seg(ENTREVISTADOR, "entendi", true));

  const speakers = t.map((s) => s.speaker);
  assert.ok(
    speakers.includes(CANDIDATO),
    `candidate interim was destroyed by the interviewer's final: ${JSON.stringify(textsBySpeaker(t))}`,
  );
});

test("a speaker's own final replaces that speaker's interim", () => {
  let t: TranscriptSegment[] = [];
  t = mergeTranscriptSegment(t, seg(CANDIDATO, "eu trabalhei", false));
  t = mergeTranscriptSegment(t, seg(CANDIDATO, "eu trabalhei dez anos", true));

  assert.deepEqual(textsBySpeaker(t), ["Candidato:eu trabalhei dez anos"]);
});

test("finals accumulate in arrival order", () => {
  let t: TranscriptSegment[] = [];
  t = mergeTranscriptSegment(t, seg(ENTREVISTADOR, "boa tarde", true));
  t = mergeTranscriptSegment(t, seg(CANDIDATO, "boa tarde", true));

  assert.deepEqual(textsBySpeaker(t), [
    "Entrevistador:boa tarde",
    "Candidato:boa tarde",
  ]);
});
