using System;

namespace TarsNativeCompanion.Capture;

public static class AudioResampler
{
    /// <summary>
    /// Converts input Float32 interleaved PCM samples to 16-bit Int16 Mono PCM at targetSampleRate (16,000 Hz).
    /// </summary>
    public static byte[] ResampleFloatTo16BitMono(
        ReadOnlySpan<float> inputSamples,
        int inputSampleRate,
        int inputChannelCount,
        int targetSampleRate = 16000)
    {
        if (inputSamples.IsEmpty) return Array.Empty<byte>();

        int inFrames = inputSamples.Length / inputChannelCount;
        if (inFrames == 0) return Array.Empty<byte>();

        // Downmix to mono float
        float[] monoFloats = new float[inFrames];
        for (int i = 0; i < inFrames; i++)
        {
            float sum = 0f;
            for (int c = 0; c < inputChannelCount; c++)
            {
                sum += inputSamples[i * inputChannelCount + c];
            }
            monoFloats[i] = sum / inputChannelCount;
        }

        if (inputSampleRate == targetSampleRate)
        {
            // Just convert float to Int16
            byte[] outBytes = new byte[inFrames * 2];
            for (int i = 0; i < inFrames; i++)
            {
                short val = ClampToShort(monoFloats[i]);
                outBytes[i * 2] = (byte)(val & 0xFF);
                outBytes[i * 2 + 1] = (byte)((val >> 8) & 0xFF);
            }
            return outBytes;
        }

        // Linear interpolation resampling
        double ratio = (double)inputSampleRate / targetSampleRate;
        int outFrames = (int)Math.Floor(inFrames / ratio);
        byte[] resampled = new byte[outFrames * 2];

        for (int i = 0; i < outFrames; i++)
        {
            double srcIdx = i * ratio;
            int idxFloor = (int)Math.Floor(srcIdx);
            int idxCeil = Math.Min(idxFloor + 1, inFrames - 1);
            double frac = srcIdx - idxFloor;

            float sample = (float)((1.0 - frac) * monoFloats[idxFloor] + frac * monoFloats[idxCeil]);
            short val = ClampToShort(sample);

            resampled[i * 2] = (byte)(val & 0xFF);
            resampled[i * 2 + 1] = (byte)((val >> 8) & 0xFF);
        }

        return resampled;
    }

    private static short ClampToShort(float val)
    {
        if (val >= 1.0f) return short.MaxValue;
        if (val <= -1.0f) return short.MinValue;
        return (short)(val * 32767f);
    }
}
