import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildTimelineEntries,
  formatGapReason,
  formatGapSource,
  formatTimeMs,
  formatTimeRange,
} from "./transcriptCoverage.ts";
import type { CoverageGapSegment, TranscriptSegment } from "@/types/ws";

test("formatTimeMs formats milliseconds to mm:ss correctly", () => {
  assert.equal(formatTimeMs(0), "00:00");
  assert.equal(formatTimeMs(5000), "00:05");
  assert.equal(formatTimeMs(65000), "01:05");
  assert.equal(formatTimeMs(600000), "10:00");
  assert.equal(formatTimeMs(-10), "00:00");
});

test("formatTimeRange formats exact range and unknown end truthfully", () => {
  assert.equal(formatTimeRange(15000, 20000), "00:15 – 00:20");
  assert.equal(formatTimeRange(60000, null), "01:00 – desconhecido");
});

test("formatGapReason provides Brazilian Portuguese descriptions", () => {
  assert.equal(formatGapReason("overrun"), "Saturação de buffer de captura");
  assert.equal(formatGapReason("device_lost"), "Dispositivo de áudio desconectado");
  assert.equal(formatGapReason("permission_denied"), "Permissão de áudio revogada");
  assert.equal(formatGapReason("buffer_exhaustion"), "Esgotamento de memória de custódia");
  assert.equal(formatGapReason("unknown"), "Interrupção de captura");
});

test("formatGapSource identifies audio channels clearly", () => {
  assert.equal(formatGapSource("microphone"), "Microfone (Entrevistador)");
  assert.equal(formatGapSource("system_audio"), "Áudio do Sistema (Candidato)");
  assert.equal(formatGapSource("both"), "Ambos os Canais");
});

test("buildTimelineEntries chronologically intersperses segments and gaps", () => {
  const seg1: TranscriptSegment = {
    id: "s1",
    text: "Olá",
    speaker: "Entrevistador",
    start_time: 1, // 1000ms
    end_time: 2,
    confidence: 0.95,
    sequence_number: 1,
    is_final: true,
  };

  const gap1: CoverageGapSegment = {
    id: "g1",
    source: "system_audio",
    start_ms: 3000,
    end_ms: 5000,
    reason: "overrun",
  };

  const seg2: TranscriptSegment = {
    id: "s2",
    text: "Tudo bem?",
    speaker: "Candidato",
    start_time: 6, // 6000ms
    end_time: 7,
    confidence: 0.92,
    sequence_number: 2,
    is_final: true,
  };

  const timeline = buildTimelineEntries([seg2, seg1], [gap1]);

  assert.equal(timeline.length, 3);
  assert.equal(timeline[0].kind, "transcript");
  assert.equal(timeline[0].sortTime, 1000);
  assert.equal(timeline[1].kind, "gap");
  assert.equal(timeline[1].sortTime, 3000);
  assert.equal(timeline[2].kind, "transcript");
  assert.equal(timeline[2].sortTime, 6000);
});
