using System;
using System.Security.Cryptography;
using System.Text;
using TarsNativeCompanion.Contracts;
using Xunit;

namespace TarsNativeCompanion.Tests;

public class AudioFrameTests
{
    [Fact]
    public void TestAudioFrameAlignmentAndDurationBounds()
    {
        var identity = new SourceIdentity("session-abc", "stream-1", 1, AudioSource.Microphone, 16000, 1);

        // Valid 50ms chunk (800 samples = 1600 bytes)
        byte[] validPayload = new byte[1600];
        var frame = new AudioFrame(identity, 1, 0, 1000, null, validPayload);
        Assert.Equal(800UL, frame.SampleCount);
        Assert.Equal(800UL, frame.LastSampleExclusive);
        Assert.Equal(50UL, frame.DurationMs);
        Assert.StartsWith("aevt_", frame.EventId);

        // Invalid unaligned payload (1599 bytes is not multiple of 2)
        byte[] unaligned = new byte[1599];
        Assert.Throws<CompanionException>(() => new AudioFrame(identity, 1, 0, 1000, null, unaligned));

        // Invalid duration (10ms chunk = 160 samples = 320 bytes, outside 20-250ms bounds)
        byte[] tooShort = new byte[320];
        Assert.Throws<CompanionException>(() => new AudioFrame(identity, 1, 0, 1000, null, tooShort));
    }

    [Fact]
    public void TestCoverageRangeAndGapEventHashing()
    {
        var identity = new SourceIdentity("sess-1", "sys-1", 1, AudioSource.SystemAudio, 16000, 1);
        var range = new CoverageRange(identity, 1, 0, 800);
        Assert.StartsWith("acov_", range.CoverageId);

        var gap = new CoverageGap(
            identity,
            firstSample: 0,
            lastSampleExclusive: 800,
            reason: GapReason.Overflow,
            firstSequence: 1,
            lastSequenceExclusive: 2,
            firstCapturedAtMs: 1000,
            lastCapturedAtMs: 1050,
            deviceId: "dev-wasapi-1",
            firstCapturedAtMonotonicNs: 1000000000UL,
            lastCapturedAtMonotonicNs: 1050000000UL,
            firstCapturedAtWallClockMs: 1000UL,
            lastCapturedAtWallClockMs: 1050UL,
            boundary: GapBoundary.KnownRange
        );

        Assert.StartsWith("gap_", gap.GapId);
        Assert.Equal(GapBoundary.KnownRange, gap.Boundary);
        Assert.Equal(GapReason.Overflow, gap.Reason);
    }
}
