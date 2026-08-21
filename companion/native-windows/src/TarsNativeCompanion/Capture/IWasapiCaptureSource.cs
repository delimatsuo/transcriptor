using System;
using System.Threading.Tasks;
using TarsNativeCompanion.Contracts;

namespace TarsNativeCompanion.Capture;

public interface ICaptureFrameSink
{
    ValueTask ReceiveFrameAsync(AudioFrame frame);
    ValueTask ReceiveGapAsync(CoverageGap gap);
}

public sealed record CaptureSourceConfiguration(
    SourceIdentity Identity,
    string DeviceIdentity
);

public interface IWasapiCaptureSource : IAsyncDisposable
{
    SourceIdentity Identity { get; }
    SourceHealth Health { get; }
    bool IsCapturing { get; }

    ValueTask StartAsync();
    ValueTask StopAsync();
}
