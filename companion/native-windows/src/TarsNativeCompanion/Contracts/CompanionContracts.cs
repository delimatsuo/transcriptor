using System;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace TarsNativeCompanion.Contracts;

public class CompanionException : Exception
{
    public CompanionException(string message) : base(message) { }
}

public enum AudioSource
{
    Microphone,
    SystemAudio
}

public static class AudioSourceExtensions
{
    public static string ToWireString(this AudioSource source) => source switch
    {
        AudioSource.Microphone => "microphone",
        AudioSource.SystemAudio => "system_audio",
        _ => throw new ArgumentOutOfRangeException(nameof(source))
    };

    public static AudioSource FromWireString(string value) => value switch
    {
        "microphone" => AudioSource.Microphone,
        "system_audio" => AudioSource.SystemAudio,
        _ => throw new ArgumentException($"Unknown audio source: {value}", nameof(value))
    };
}

public enum PermissionState
{
    Unknown,
    Granted,
    Denied,
    Revoked
}

public enum RouteState
{
    Unknown,
    Healthy,
    Unavailable,
    Ambiguous,
    Changed
}

public enum InterruptionState
{
    Clear,
    Interrupted
}

public enum SleepState
{
    Awake,
    Sleeping,
    Woke
}

public record SourceHealth(
    PermissionState Permission = PermissionState.Unknown,
    RouteState Route = RouteState.Unknown,
    InterruptionState Interruption = InterruptionState.Clear,
    SleepState Sleep = SleepState.Awake,
    bool Overflowed = false,
    string? DeviceIdentity = null
)
{
    public bool IsHealthy =>
        Permission == PermissionState.Granted &&
        Route == RouteState.Healthy &&
        Interruption == InterruptionState.Clear &&
        Sleep != SleepState.Sleeping &&
        !Overflowed &&
        (DeviceIdentity != null && SourceIdentity.IsIdentifier(DeviceIdentity));
}

public sealed record SourceIdentity
{
    public string SessionId { get; }
    public string StreamId { get; }
    public ulong CaptureGeneration { get; }
    public AudioSource Source { get; }
    public int SampleRate { get; }
    public int ChannelCount { get; }

    public SourceIdentity(
        string sessionId,
        string streamId,
        ulong captureGeneration,
        AudioSource source,
        int sampleRate,
        int channelCount)
    {
        if (!IsIdentifier(sessionId) || !IsIdentifier(streamId))
        {
            throw new CompanionException("session and stream identifiers must be ASCII identifiers");
        }
        if (sampleRate < 8000 || sampleRate > 48000 || channelCount < 1 || channelCount > 2)
        {
            throw new CompanionException("sample rate or channel count is outside the offline bounds");
        }

        SessionId = sessionId;
        StreamId = streamId;
        CaptureGeneration = captureGeneration;
        Source = source;
        SampleRate = sampleRate;
        ChannelCount = channelCount;
    }

    public string Key => $"{SessionId}\0{StreamId}\0{CaptureGeneration}\0{Source.ToWireString()}";

    public static bool IsIdentifier(string value)
    {
        if (string.IsNullOrEmpty(value) || value.Length > 128) return false;
        byte[] bytes = Encoding.UTF8.GetBytes(value);
        byte first = bytes[0];
        if (!((first >= 48 && first <= 57) || (first >= 65 && first <= 90) || (first >= 97 && first <= 122)))
        {
            return false;
        }
        for (int i = 1; i < bytes.Length; i++)
        {
            byte b = bytes[i];
            bool valid = (b >= 48 && b <= 57) ||
                         (b >= 65 && b <= 90) ||
                         (b >= 97 && b <= 122) ||
                         b == 45 || b == 46 || b == 58 || b == 95; // '-', '.', ':', '_'
            if (!valid) return false;
        }
        return true;
    }
}

public sealed record CaptureEventContext
{
    public string DeviceId { get; }
    public ulong CapturedAtMonotonicNs { get; }
    public ulong CapturedAtWallClockMs { get; }

    public CaptureEventContext(string deviceId, ulong capturedAtMonotonicNs, ulong capturedAtWallClockMs)
    {
        if (!SourceIdentity.IsIdentifier(deviceId))
        {
            throw new CompanionException("device identifier is invalid");
        }
        DeviceId = deviceId;
        CapturedAtMonotonicNs = capturedAtMonotonicNs;
        CapturedAtWallClockMs = capturedAtWallClockMs;
    }

    public static CaptureEventContext CreateFixture(ulong capturedAtMs) =>
        new("unknown-device", capturedAtMs * 1_000_000UL, capturedAtMs);
}

public sealed class SecureAudioBuffer : IEquatable<SecureAudioBuffer>
{
    private readonly object _lock = new();
    private byte[] _storage;

    public SecureAudioBuffer(byte[] data)
    {
        _storage = (byte[])data.Clone();
    }

    public int Count
    {
        get
        {
            lock (_lock) { return _storage.Length; }
        }
    }

    public bool IsEmpty => Count == 0;

    public byte[] CopyData()
    {
        lock (_lock)
        {
            return (byte[])_storage.Clone();
        }
    }

    internal void Zeroize()
    {
        lock (_lock)
        {
            Array.Clear(_storage, 0, _storage.Length);
        }
    }

    public bool Equals(SecureAudioBuffer? other)
    {
        if (other is null) return false;
        if (ReferenceEquals(this, other)) return true;
        byte[] a = CopyData();
        byte[] b = other.CopyData();
        return a.SequenceEqual(b);
    }

    public override bool Equals(object? obj) => Equals(obj as SecureAudioBuffer);

    public override int GetHashCode()
    {
        byte[] a = CopyData();
        return a.Length > 0 ? a[0].GetHashCode() : 0;
    }
}

public sealed record AudioFrame
{
    public SourceIdentity Identity { get; }
    public ulong Sequence { get; }
    public ulong FirstSample { get; }
    public ulong CapturedAtMs { get; }
    public CaptureEventContext EventContext { get; }
    public SecureAudioBuffer Payload { get; }

    public AudioFrame(
        SourceIdentity identity,
        ulong sequence,
        ulong firstSample,
        ulong capturedAtMs,
        CaptureEventContext? eventContext,
        byte[] payloadData)
    {
        int bytesPerSample = identity.ChannelCount * 2;
        if (payloadData.Length == 0 || payloadData.Length % bytesPerSample != 0)
        {
            throw new CompanionException("frame payload is not aligned to channel samples");
        }
        int sampleCount = payloadData.Length / bytesPerSample;
        int durationNumerator = sampleCount * 1000;
        int durationMs = durationNumerator / identity.SampleRate;
        if (durationMs < 20 || durationMs > 250 || durationNumerator % identity.SampleRate != 0)
        {
            throw new CompanionException("frame duration is outside the canonical 20-250 ms bounds");
        }

        Identity = identity;
        Sequence = sequence;
        FirstSample = firstSample;
        CapturedAtMs = capturedAtMs;
        EventContext = eventContext ?? CaptureEventContext.CreateFixture(capturedAtMs);
        Payload = new SecureAudioBuffer(payloadData);
    }

    public ulong SampleCount => (ulong)(Payload.Count / (Identity.ChannelCount * 2));
    public ulong LastSampleExclusive => FirstSample + SampleCount;
    public ulong DurationMs => SampleCount * 1000UL / (ulong)Identity.SampleRate;

    public string EventId
    {
        get
        {
            string[] fields = [
                "tars-audio-event-v2",
                Identity.SessionId,
                Identity.StreamId,
                Identity.CaptureGeneration.ToString(),
                Identity.Source.ToWireString(),
                Sequence.ToString(),
                FirstSample.ToString(),
                LastSampleExclusive.ToString()
            ];
            byte[] bytes = Encoding.UTF8.GetBytes(string.Join("\0", fields));
            using var sha256 = SHA256.Create();
            byte[] hash = sha256.ComputeHash(bytes);
            return "aevt_" + Convert.ToHexString(hash).ToLowerInvariant();
        }
    }
}

public sealed record CoverageRange
{
    public SourceIdentity Identity { get; }
    public ulong Sequence { get; }
    public ulong FirstSample { get; }
    public ulong LastSampleExclusive { get; }

    public CoverageRange(AudioFrame frame)
    {
        Identity = frame.Identity;
        Sequence = frame.Sequence;
        FirstSample = frame.FirstSample;
        LastSampleExclusive = frame.LastSampleExclusive;
    }

    public CoverageRange(SourceIdentity identity, ulong sequence, ulong firstSample, ulong lastSampleExclusive)
    {
        if (lastSampleExclusive <= firstSample)
        {
            throw new CompanionException("coverage range is empty");
        }
        Identity = identity;
        Sequence = sequence;
        FirstSample = firstSample;
        LastSampleExclusive = lastSampleExclusive;
    }

    public string CoverageId
    {
        get
        {
            string[] fields = [
                "tars-atomic-coverage-v2",
                Identity.SessionId,
                Identity.StreamId,
                Identity.CaptureGeneration.ToString(),
                Identity.Source.ToWireString(),
                Sequence.ToString(),
                FirstSample.ToString(),
                LastSampleExclusive.ToString()
            ];
            byte[] bytes = Encoding.UTF8.GetBytes(string.Join("\0", fields));
            using var sha256 = SHA256.Create();
            byte[] hash = sha256.ComputeHash(bytes);
            return "acov_" + Convert.ToHexString(hash).ToLowerInvariant();
        }
    }
}

public enum GapReason
{
    Overflow,
    RouteLoss,
    PermissionRevoked,
    ForcedTermination,
    LocalPrivacyDiscard,
    PrivacyTimeout,
    UnknownEnd,
    StaleGeneration
}

public static class GapReasonExtensions
{
    public static string ToWireString(this GapReason reason) => reason switch
    {
        GapReason.Overflow => "overflow",
        GapReason.RouteLoss => "route_loss",
        GapReason.PermissionRevoked => "permission_revoked",
        GapReason.ForcedTermination => "forced_termination",
        GapReason.LocalPrivacyDiscard => "local_privacy_discard",
        GapReason.PrivacyTimeout => "privacy_timeout",
        GapReason.UnknownEnd => "unknown_end",
        GapReason.StaleGeneration => "stale_generation",
        _ => throw new ArgumentOutOfRangeException(nameof(reason))
    };
}

public enum GapBoundary
{
    KnownRange,
    UnknownEnd
}

public sealed record CoverageGap
{
    public SourceIdentity Identity { get; }
    public ulong? FirstSample { get; }
    public ulong? LastSampleExclusive { get; }
    public ulong? FirstSequence { get; }
    public ulong? LastSequenceExclusive { get; }
    public ulong? FirstCapturedAtMs { get; }
    public ulong? LastCapturedAtMs { get; }
    public string? DeviceId { get; }
    public ulong? FirstCapturedAtMonotonicNs { get; }
    public ulong? LastCapturedAtMonotonicNs { get; }
    public ulong? FirstCapturedAtWallClockMs { get; }
    public ulong? LastCapturedAtWallClockMs { get; }
    public GapBoundary Boundary { get; }
    public GapReason Reason { get; }

    public CoverageGap(
        SourceIdentity identity,
        ulong? firstSample,
        ulong? lastSampleExclusive,
        GapReason reason,
        ulong? firstSequence = null,
        ulong? lastSequenceExclusive = null,
        ulong? firstCapturedAtMs = null,
        ulong? lastCapturedAtMs = null,
        string? deviceId = null,
        ulong? firstCapturedAtMonotonicNs = null,
        ulong? lastCapturedAtMonotonicNs = null,
        ulong? firstCapturedAtWallClockMs = null,
        ulong? lastCapturedAtWallClockMs = null,
        GapBoundary? boundary = null)
    {
        if (firstSequence is null)
        {
            throw new CompanionException("gap requires a first sequence");
        }
        if (firstSample.HasValue && lastSampleExclusive.HasValue && lastSampleExclusive <= firstSample)
        {
            throw new CompanionException("gap range is empty");
        }
        if (lastSequenceExclusive.HasValue && lastSequenceExclusive <= firstSequence)
        {
            throw new CompanionException("gap sequence range is empty");
        }
        if (!firstSample.HasValue && lastSampleExclusive.HasValue)
        {
            throw new CompanionException("gap sample end requires a sample start");
        }

        GapBoundary resolvedBoundary = boundary ?? (lastSampleExclusive is null ? GapBoundary.UnknownEnd : GapBoundary.KnownRange);
        if (resolvedBoundary == GapBoundary.KnownRange)
        {
            if (!firstSample.HasValue || !lastSampleExclusive.HasValue || !lastSequenceExclusive.HasValue)
            {
                throw new CompanionException("known gap requires complete sample and sequence bounds");
            }
        }
        else
        {
            if (lastSampleExclusive.HasValue || lastSequenceExclusive.HasValue)
            {
                throw new CompanionException("unknown-end gap cannot claim a terminal end");
            }
        }

        if (deviceId is null || !SourceIdentity.IsIdentifier(deviceId))
        {
            throw new CompanionException("gap device identifier is required and invalid");
        }
        if (!firstCapturedAtMonotonicNs.HasValue || !firstCapturedAtWallClockMs.HasValue)
        {
            throw new CompanionException("gap requires first monotonic and wall-clock timestamps");
        }
        if (firstCapturedAtMs.HasValue && lastCapturedAtMs.HasValue && lastCapturedAtMs < firstCapturedAtMs)
        {
            throw new CompanionException("gap timestamps are reversed");
        }
        if (firstCapturedAtMonotonicNs.HasValue && lastCapturedAtMonotonicNs.HasValue && lastCapturedAtMonotonicNs < firstCapturedAtMonotonicNs)
        {
            throw new CompanionException("gap monotonic timestamps are reversed");
        }
        if (firstCapturedAtWallClockMs.HasValue && lastCapturedAtWallClockMs.HasValue && lastCapturedAtWallClockMs < firstCapturedAtWallClockMs)
        {
            throw new CompanionException("gap wall-clock timestamps are reversed");
        }

        Identity = identity;
        FirstSample = firstSample;
        LastSampleExclusive = lastSampleExclusive;
        FirstSequence = firstSequence;
        LastSequenceExclusive = lastSequenceExclusive;
        FirstCapturedAtMs = firstCapturedAtMs;
        LastCapturedAtMs = lastCapturedAtMs;
        DeviceId = deviceId;
        FirstCapturedAtMonotonicNs = firstCapturedAtMonotonicNs;
        LastCapturedAtMonotonicNs = lastCapturedAtMonotonicNs;
        FirstCapturedAtWallClockMs = firstCapturedAtWallClockMs;
        LastCapturedAtWallClockMs = lastCapturedAtWallClockMs;
        Boundary = resolvedBoundary;
        Reason = reason;
    }

    public string GapId
    {
        get
        {
            string[] fields = [
                "tars-gap-v2",
                Identity.SessionId,
                Identity.StreamId,
                Identity.CaptureGeneration.ToString(),
                Identity.Source.ToWireString(),
                FirstSequence?.ToString() ?? "?",
                LastSequenceExclusive?.ToString() ?? "?",
                FirstSample?.ToString() ?? "?",
                LastSampleExclusive?.ToString() ?? "?",
                FirstCapturedAtMs?.ToString() ?? "?",
                LastCapturedAtMs?.ToString() ?? "?",
                DeviceId ?? "?",
                FirstCapturedAtMonotonicNs?.ToString() ?? "?",
                LastCapturedAtMonotonicNs?.ToString() ?? "?",
                FirstCapturedAtWallClockMs?.ToString() ?? "?",
                LastCapturedAtWallClockMs?.ToString() ?? "?",
                Boundary == GapBoundary.KnownRange ? "known_range" : "unknown_end",
                Reason.ToWireString()
            ];
            byte[] bytes = Encoding.UTF8.GetBytes(string.Join("\0", fields));
            using var sha256 = SHA256.Create();
            byte[] hash = sha256.ComputeHash(bytes);
            return "gap_" + Convert.ToHexString(hash).ToLowerInvariant();
        }
    }
}

public enum PhysicalCaptureState
{
    SetupRequired,
    CheckingPermissionsAndDevices,
    ReadyBothSources,
    Starting,
    Capturing,
    Paused,
    Stopping,
    Stopped,
    Degraded,
    Deleting
}
