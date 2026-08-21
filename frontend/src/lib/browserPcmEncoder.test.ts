import { test } from "node:test";
import assert from "node:assert/strict";
import {
  resampleTo16k,
  float32ToInt16,
  encodeAudioFrame,
  type FrameMetadata,
} from "./browserPcmEncoder.ts";

test("resampleTo16k returns identical array when sample rate is already 16000", () => {
  const input = new Float32Array([0.0, 0.5, -0.5, 1.0]);
  const resampled = resampleTo16k(input, 16000, 16000);
  assert.deepEqual(resampled, input);
});

test("resampleTo16k downsamples 48kHz input to 1/3 sample count", () => {
  const input = new Float32Array(48000); // 1 second of 48kHz
  for (let i = 0; i < input.length; i++) {
    input[i] = Math.sin((2 * Math.PI * 440 * i) / 48000);
  }
  const resampled = resampleTo16k(input, 48000, 16000);
  assert.equal(resampled.length, 16000);
});

test("float32ToInt16 correctly clips and maps audio samples to Int16 bounds", () => {
  const input = new Float32Array([-1.5, -1.0, 0.0, 0.5, 1.0, 1.5]);
  const result = float32ToInt16(input);

  assert.equal(result.length, 6);
  assert.equal(result[0], -32768); // Clipped min
  assert.equal(result[1], -32768); // Min
  assert.equal(result[2], 0);      // Zero
  assert.ok(result[3] > 16300 && result[3] < 16400); // ~0.5 * 32767
  assert.equal(result[4], 32767);  // Max
  assert.equal(result[5], 32767);  // Clipped max
});

test("encodeAudioFrame creates valid wire packet with big-endian header length", () => {
  const meta: FrameMetadata = {
    session_id: "session-test-01",
    source: "microphone",
    sequence: 1,
    first_sample: 0,
    captured_at_ms: 1000,
    duration_ms: 50,
    sample_rate: 16000,
    channel_count: 1,
  };
  const pcm = new Int16Array(800); // 50ms at 16kHz
  pcm.fill(1234);

  const packetBuffer = encodeAudioFrame(meta, pcm);
  const view = new DataView(packetBuffer);

  const headerLen = view.getUint32(0, false); // big-endian
  assert.ok(headerLen > 0);

  const headerBytes = new Uint8Array(packetBuffer, 4, headerLen);
  const decoder = new TextDecoder();
  const parsedMeta = JSON.parse(decoder.decode(headerBytes));

  assert.equal(parsedMeta.session_id, "session-test-01");
  assert.equal(parsedMeta.source, "microphone");
  assert.equal(parsedMeta.duration_ms, 50);

  const audioBytes = new Uint8Array(packetBuffer, 4 + headerLen);
  assert.equal(audioBytes.byteLength, 1600); // 800 samples * 2 bytes

  const audioInt16 = new Int16Array(
    packetBuffer.slice(4 + headerLen),
  );
  assert.equal(audioInt16[0], 1234);
  assert.equal(audioInt16[799], 1234);
});
