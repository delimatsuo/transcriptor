using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using TarsNativeCompanion.Contracts;

namespace TarsNativeCompanion.Capture;

public sealed class WasapiMicrophoneAudioSource : IWasapiCaptureSource
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

    private readonly bool _simulated;
    private CancellationTokenSource? _simCts;
    private Task? _simTask;

    public WasapiMicrophoneAudioSource(
        CaptureSourceConfiguration configuration,
        bool liveCaptureEnabled = true,
        ICaptureFrameSink? sink = null,
        bool simulated = false)
    {
        _configuration = configuration;
        _liveCaptureEnabled = liveCaptureEnabled;
        _sink = sink;
        _simulated = simulated;
        Health = new SourceHealth(
            Permission: PermissionState.Granted,
            Route: RouteState.Healthy,
            Interruption: InterruptionState.Clear,
            Sleep: SleepState.Awake,
            Overflowed: false,
            DeviceIdentity: configuration.DeviceIdentity
        );
    }

    private NAudio.CoreAudioApi.WasapiCapture? _wasapiCapture;

    public ValueTask StartAsync()
    {
        lock (_lock)
        {
            if (_capturing) return ValueTask.CompletedTask;
            _capturing = true;
            _sequenceNumber = 1;
            _currentSampleOffset = 0;
            _buffer.Clear();
            if (_simulated)
            {
                _simCts = new CancellationTokenSource();
                _simTask = Task.Run(() => RunSimulationAsync(_simCts.Token));
            }
            else if (OperatingSystem.IsWindows() && _liveCaptureEnabled)
            {
                StartLiveCapture();
            }
        }
        return ValueTask.CompletedTask;
    }

    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    private void StartLiveCapture()
    {
        try
        {
            _wasapiCapture = new NAudio.CoreAudioApi.WasapiCapture();
            var format = _wasapiCapture.WaveFormat;
            int sampleRate = format.SampleRate;
            int channels = format.Channels;
            bool isFloat = format.Encoding == NAudio.Wave.WaveFormatEncoding.IeeeFloat;

            _wasapiCapture.DataAvailable += async (s, e) =>
            {
                if (!_capturing || e.BytesRecorded == 0) return;
                ulong nowMs = (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

                if (isFloat)
                {
                    int floatCount = e.BytesRecorded / 4;
                    float[] floatSamples = new float[floatCount];
                    Buffer.BlockCopy(e.Buffer, 0, floatSamples, 0, e.BytesRecorded);
                    await PushSamplesAsync(floatSamples, sampleRate, channels, nowMs);
                }
                else
                {
                    int shortCount = e.BytesRecorded / 2;
                    float[] floatSamples = new float[shortCount];
                    for (int i = 0; i < shortCount; i++)
                    {
                        short val = BitConverter.ToInt16(e.Buffer, i * 2);
                        floatSamples[i] = val / 32768.0f;
                    }
                    await PushSamplesAsync(floatSamples, sampleRate, channels, nowMs);
                }
            };

            _wasapiCapture.RecordingStopped += (s, e) =>
            {
                if (e.Exception != null)
                {
                    Health = Health with { Route = RouteState.Unavailable };
                }
            };

            _wasapiCapture.StartRecording();
        }
        catch (Exception)
        {
            Health = Health with { Route = RouteState.Unavailable };
            throw;
        }
    }

    public async ValueTask StopAsync()
    {
        CancellationTokenSource? cts;
        Task? task;
        lock (_lock)
        {
            _capturing = false;
            _buffer.Clear();
            cts = _simCts;
            task = _simTask;
            _simCts = null;
            _simTask = null;

            if (OperatingSystem.IsWindows() && _wasapiCapture != null)
            {
                StopLiveCapture();
            }
        }
        if (cts != null)
        {
            cts.Cancel();
            if (task != null)
            {
                try { await task; } catch { }
            }
            cts.Dispose();
        }
    }

    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    private void StopLiveCapture()
    {
        try
        {
            _wasapiCapture?.StopRecording();
            _wasapiCapture?.Dispose();
            _wasapiCapture = null;
        }
        catch { }
    }

    private async Task RunSimulationAsync(CancellationToken ct)
    {
        float[] samples = new float[800]; // 50ms at 16kHz
        for (int i = 0; i < samples.Length; i++)
        {
            samples[i] = 0.2f * (float)Math.Sin(2.0 * Math.PI * 440.0 * i / 16000.0);
        }

        while (!ct.IsCancellationRequested && _capturing)
        {
            ulong nowMs = (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            await PushSamplesAsync(samples, 16000, 1, nowMs);
            try
            {
                await Task.Delay(50, ct);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

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
