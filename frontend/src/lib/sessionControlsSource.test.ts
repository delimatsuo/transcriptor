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
