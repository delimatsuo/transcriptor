/**
 * Browser PCM audio downsampler and wire protocol framing encoder.
 *
 * Converts Web Audio Float32 samples from native browser sample rate (e.g. 44.1kHz or 48kHz)
 * into 16,000 Hz Int16 mono linear PCM and packages binary frames matching the T.A.R.S.
 * native stream gateway protocol (4-byte big-endian JSON header length + JSON + Int16 PCM).
 */

export interface FrameMetadata {
  session_id: string;
  source: "microphone" | "system_audio";
  sequence: number;
  first_sample: number;
  captured_at_ms: number;
  duration_ms: number;
  sample_rate: number;
  channel_count: number;
}

/**
 * Resample a Float32Array mono buffer from sourceSampleRate to targetSampleRate (16000Hz).
 */
export function resampleTo16k(
  input: Float32Array,
  sourceSampleRate: number,
  targetSampleRate = 16000,
): Float32Array {
  if (sourceSampleRate === targetSampleRate) {
    return input;
  }
  const ratio = sourceSampleRate / targetSampleRate;
  const newLength = Math.round(input.length / ratio);
  const result = new Float32Array(newLength);

  for (let i = 0; i < newLength; i++) {
    const srcIndex = i * ratio;
    const i0 = Math.floor(srcIndex);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = srcIndex - i0;
    result[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }

  return result;
}

/**
 * Convert Float32Array (-1.0 to 1.0) to Int16Array (-32768 to 32767).
 */
export function float32ToInt16(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    output[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return output;
}

/**
 * Build wire protocol binary packet:
 * [4-byte big-endian uint32 header_length] [UTF-8 JSON header] [Int16 LE audio bytes]
 */
export function encodeAudioFrame(
  meta: FrameMetadata,
  pcmInt16: Int16Array,
): ArrayBuffer {
  const headerJson = JSON.stringify(meta);
  const encoder = new TextEncoder();
  const headerBytes = encoder.encode(headerJson);
  const headerLen = headerBytes.length;

  const audioByteLen = pcmInt16.byteLength;
  const totalLen = 4 + headerLen + audioByteLen;

  const buffer = new ArrayBuffer(totalLen);
  const dataView = new DataView(buffer);

  // 4-byte big-endian header length
  dataView.setUint32(0, headerLen, false);

  // Copy header bytes
  const uint8Buffer = new Uint8Array(buffer);
  uint8Buffer.set(headerBytes, 4);

  // Copy audio bytes
  const audioUint8 = new Uint8Array(
    pcmInt16.buffer,
    pcmInt16.byteOffset,
    pcmInt16.byteLength,
  );
  uint8Buffer.set(audioUint8, 4 + headerLen);

  return buffer;
}
