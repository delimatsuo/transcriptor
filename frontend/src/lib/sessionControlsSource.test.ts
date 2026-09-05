import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

test("SessionControls renders AudioDeviceSelector unconditionally outside showInterviewPrep", () => {
  const sourceUrl = new URL("../components/SessionControls.tsx", import.meta.url);
  const source = readFileSync(sourceUrl, "utf8");

  const selectorMatches = source.match(/<AudioDeviceSelector/g) ?? [];
  assert.equal(
    selectorMatches.length,
    1,
    "source must contain exactly one <AudioDeviceSelector occurrence",
  );

  const selectorIndex = source.indexOf("<AudioDeviceSelector");
  const showInterviewPrepIndex = source.indexOf("{showInterviewPrep && (");

  assert.notEqual(selectorIndex, -1, "Expected <AudioDeviceSelector in source");
  assert.notEqual(
    showInterviewPrepIndex,
    -1,
    "Expected {showInterviewPrep && ( in source",
  );
  assert.ok(
    selectorIndex < showInterviewPrepIndex,
    "<AudioDeviceSelector must precede {showInterviewPrep && (",
  );

  const audioCaptureIndex = source.lastIndexOf(
    "{audioCapture && (",
    selectorIndex,
  );
  assert.notEqual(
    audioCaptureIndex,
    -1,
    "Expected {audioCapture && ( before <AudioDeviceSelector",
  );
  assert.ok(
    audioCaptureIndex < selectorIndex,
    "{audioCapture && ( must precede <AudioDeviceSelector",
  );
});

test("SessionControls integrates Workable scheduled interviews and automated job/candidate selectors", () => {
  const sourceUrl = new URL("../components/SessionControls.tsx", import.meta.url);
  const source = readFileSync(sourceUrl, "utf8");

  // Verifies calendar/upcoming endpoint call
  assert.ok(
    source.includes("/api/calendar/upcoming"),
    "Expected SessionControls to query /api/calendar/upcoming",
  );

  // Verifies workable jobs endpoint call
  assert.ok(
    source.includes("/api/integrations/workable/jobs"),
    "Expected SessionControls to query /api/integrations/workable/jobs",
  );

  // Verifies scheduled interviews section
  assert.ok(
    source.includes("Entrevistas Agendadas (Workable & Calendário)"),
    "Expected scheduled interviews heading in SessionControls",
  );

  // Verifies 1-click load button
  assert.ok(
    source.includes("Carregar Entrevista"),
    "Expected 'Carregar Entrevista' button in SessionControls",
  );

  // Verifies collapsible manual form toggle
  assert.ok(
    source.includes("showManualForm"),
    "Expected showManualForm state toggle in SessionControls",
  );
  assert.ok(
    source.includes("Ocultar preenchimento manual"),
    "Expected toggle label for manual inputs",
  );
});
