using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using TarsNativeCompanion.Contracts;

namespace TarsNativeCompanion.Capture;

public sealed class WasapiLoopbackSystemAudioSource : IWasapiCaptureSource
{
    private readonly CaptureSourceConfiguration _configuration;
    private readonly bool _liveCaptureEnabled;
    private readonly ICaptureFrameSink? _sink;
    private readonly List<byte> _buffer = new();
    private readonly object _lock = new();

    private ulong _sequenceNumber;
    private ulong _currentSampleOffset;
    private bool _capturing;

    public SourceIdentity Identity => _configuration.Identity;
    public SourceHealth Health { get; private set; }
    public bool IsCapturing => _capturing;

    public WasapiLoopbackSystemAudioSource(
        CaptureSourceConfiguration configuration,
        bool liveCaptureEnabled = true,
        ICaptureFrameSink? sink = null)
    {
        _configuration = configuration;
        _liveCaptureEnabled = liveCaptureEnabled;
        _sink = sink;
        Health = new SourceHealth(
            Permission: PermissionState.Granted,
            Route: RouteState.Healthy,
            Interruption: InterruptionState.Clear,
            Sleep: SleepState.Awake,
            Overflowed: false,
            DeviceIdentity: configuration.DeviceIdentity
        );
    }

    public ValueTask StartAsync()
    {
        lock (_lock)
        {
            if (_capturing) return ValueTask.CompletedTask;
            _capturing = true;
            _sequenceNumber = 1;
            _currentSampleOffset = 0;
            _buffer.Clear();
        }
        return ValueTask.CompletedTask;
    }

    public ValueTask StopAsync()
    {
        lock (_lock)
        {
            _capturing = false;
            _buffer.Clear();
        }
        return ValueTask.CompletedTask;
    }

    /// <summary>
    /// Processes incoming float audio buffers from WASAPI Loopback capture,
    /// converts them into canonical 50ms 16kHz Int16 linear PCM AudioFrames,
    /// and delivers them to the frame sink.
    /// </summary>
    public async ValueTask PushSamplesAsync(ReadOnlyMemory<float> floatSamples, int sampleRate, int channels, ulong captureTimeMs)
    {
        if (!_capturing) return;

        byte[] pcmBytes = AudioResampler.ResampleFloatTo16BitMono(
            floatSamples.Span,
            sampleRate,
            channels,
            Identity.SampleRate
        );

        if (pcmBytes.Length == 0) return;

        // Canonical 50ms chunk size: (16000 samples/sec * 0.050 sec * 2 bytes/sample) = 1600 bytes
        int targetChunkBytes = (Identity.SampleRate * 50 / 1000) * (Identity.ChannelCount * 2);

        List<byte[]> framesToEmit = new();
        lock (_lock)
        {
            _buffer.AddRange(pcmBytes);
            while (_buffer.Count >= targetChunkBytes)
            {
                byte[] chunk = _buffer.GetRange(0, targetChunkBytes).ToArray();
                _buffer.RemoveRange(0, targetChunkBytes);
                framesToEmit.Add(chunk);
            }
        }

        if (_sink != null)
        {
            foreach (byte[] chunk in framesToEmit)
            {
                ulong seq;
                ulong firstSample;
                lock (_lock)
                {
                    seq = _sequenceNumber++;
                    firstSample = _currentSampleOffset;
                    _currentSampleOffset += (ulong)(chunk.Length / (Identity.ChannelCount * 2));
                }

                var frame = new AudioFrame(
                    Identity,
                    seq,
                    firstSample,
                    captureTimeMs,
                    CaptureEventContext.CreateFixture(captureTimeMs),
                    chunk
                );

                await _sink.ReceiveFrameAsync(frame);
            }
        }
    }

    public ValueTask DisposeAsync()
    {
        return StopAsync();
    }
}
