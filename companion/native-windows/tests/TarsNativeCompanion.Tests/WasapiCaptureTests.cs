using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using TarsNativeCompanion.Capture;
using TarsNativeCompanion.Contracts;
using Xunit;

namespace TarsNativeCompanion.Tests;

public class WasapiCaptureTests
{
    private class TestFrameSink : ICaptureFrameSink
    {
        public List<AudioFrame> Frames { get; } = new();
        public List<CoverageGap> Gaps { get; } = new();

        public ValueTask ReceiveFrameAsync(AudioFrame frame)
        {
            Frames.Add(frame);
            return ValueTask.CompletedTask;
        }

        public ValueTask ReceiveGapAsync(CoverageGap gap)
        {
            Gaps.Add(gap);
            return ValueTask.CompletedTask;
        }
    }

    [Fact]
    public async Task TestWasapiLoopbackResamplesAndEmitsCanonical50msFrames()
    {
        var identity = new SourceIdentity("sess-loopback", "sys-1", 1, AudioSource.SystemAudio, 16000, 1);
        var config = new CaptureSourceConfiguration(identity, "Wasapi.DefaultRender");
        var sink = new TestFrameSink();
        var source = new WasapiLoopbackSystemAudioSource(config, liveCaptureEnabled: true, sink: sink);

        await source.StartAsync();
        Assert.True(source.IsCapturing);
        Assert.True(source.Health.IsHealthy);

        // Feed 100ms of 48kHz stereo float audio = 4800 stereo frames = 9600 float samples
        float[] stereo48k = new float[4800 * 2];
        for (int i = 0; i < stereo48k.Length; i++)
        {
            stereo48k[i] = 0.5f * (float)Math.Sin(2.0 * Math.PI * 440.0 * (i / 2) / 48000.0);
        }

        await source.PushSamplesAsync(stereo48k, 48000, 2, captureTimeMs: 1000);

        // 100ms of audio should produce exactly two 50ms canonical AudioFrames
        Assert.Equal(2, sink.Frames.Count);

        var frame1 = sink.Frames[0];
        Assert.Equal(1UL, frame1.Sequence);
        Assert.Equal(0UL, frame1.FirstSample);
        Assert.Equal(800UL, frame1.SampleCount);
        Assert.Equal(50UL, frame1.DurationMs);
        Assert.Equal(1000UL, frame1.CapturedAtMs);
        Assert.Equal(1600, frame1.Payload.Count);

        var frame2 = sink.Frames[1];
        Assert.Equal(2UL, frame2.Sequence);
        Assert.Equal(800UL, frame2.FirstSample);
        Assert.Equal(800UL, frame2.SampleCount);
        Assert.Equal(50UL, frame2.DurationMs);
        Assert.Equal(1050UL, frame2.CapturedAtMs);
        Assert.Equal(1600, frame2.Payload.Count);

        await source.StopAsync();
        Assert.False(source.IsCapturing);
    }

    [Fact]
    public async Task TestWasapiMicrophoneCaptureResamplesAndEmitsFrames()
    {
        var identity = new SourceIdentity("sess-mic", "mic-1", 1, AudioSource.Microphone, 16000, 1);
        var config = new CaptureSourceConfiguration(identity, "Wasapi.DefaultMic");
        var sink = new TestFrameSink();
        var source = new WasapiMicrophoneAudioSource(config, liveCaptureEnabled: true, sink: sink);

        await source.StartAsync();
        Assert.True(source.IsCapturing);

        // Feed 50ms of 44.1kHz mono float audio = 2205 samples
        float[] mono44k = new float[2205];
        for (int i = 0; i < mono44k.Length; i++)
        {
            mono44k[i] = 0.25f;
        }

        await source.PushSamplesAsync(mono44k, 44100, 1, captureTimeMs: 2000);

        Assert.Single(sink.Frames);
        var frame = sink.Frames[0];
        Assert.Equal(1UL, frame.Sequence);
        Assert.Equal(800UL, frame.SampleCount);
        Assert.Equal(50UL, frame.DurationMs);
        Assert.Equal(2000UL, frame.CapturedAtMs);
        Assert.Equal(1600, frame.Payload.Count);

        await source.StopAsync();
    }
}
