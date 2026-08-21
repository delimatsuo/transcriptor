using System;
using TarsNativeCompanion.Contracts;
using TarsNativeCompanion.Memory;
using Xunit;

namespace TarsNativeCompanion.Tests;

public class CustodyRingTests
{
    private static SourceIdentity CreateIdentity(string session = "sess-1", string stream = "mic") =>
        new(session, stream, 1, AudioSource.Microphone, 16000, 1);

    private static AudioFrame CreateFrame(SourceIdentity identity, ulong sequence, ulong capturedAtMs = 1000)
    {
        // 50ms @ 16kHz mono = 800 samples = 1600 bytes
        byte[] payload = new byte[1600];
        return new AudioFrame(identity, sequence, (sequence - 1) * 800, capturedAtMs, null, payload);
    }

    [Fact]
    public void TestTwoSecondsIsARealDurationBound()
    {
        var identity = CreateIdentity();
        var limits = new CustodyLimits(identity.SampleRate, identity.ChannelCount);
        var ring = new CustodyRing(limits);

        // 40 frames of 50ms = 2000ms
        for (ulong i = 1; i <= 40; i++)
        {
            var frame = CreateFrame(identity, i);
            var entry = new CustodyEntry(frame);
            bool reserved = ring.Reserve(entry);
            Assert.True(reserved);
        }

        Assert.Equal(40, ring.RetainedCount);
        Assert.Equal(2000UL, ring.RetainedDurationMs);

        // 41st frame exceeds 2000ms max duration
        var overflowFrame = CreateFrame(identity, 41);
        var overflowEntry = new CustodyEntry(overflowFrame);
        Assert.Throws<CompanionException>(() => ring.Reserve(overflowEntry));
        Assert.True(ring.AcquisitionStopped);
    }

    [Fact]
    public void TestCapacityBoundRequiresExactReservationRollback()
    {
        var identity = CreateIdentity();
        var limits = new CustodyLimits(identity.SampleRate, identity.ChannelCount);
        var ring = new CustodyRing(limits);

        var frame = CreateFrame(identity, 1);
        var entry = new CustodyEntry(frame);
        ring.Reserve(entry);
        Assert.Equal(1, ring.RetainedCount);

        ring.RollbackReservation(frame.EventId);
        Assert.Equal(0, ring.RetainedCount);
        Assert.True(frame.Payload.CopyData().All(b => b == 0));
    }

    [Fact]
    public void TestDuplicateEventRejection()
    {
        var identity = CreateIdentity();
        var limits = new CustodyLimits(identity.SampleRate, identity.ChannelCount);
        var ring = new CustodyRing(limits);

        var frame = CreateFrame(identity, 1);
        var entry = new CustodyEntry(frame);
        Assert.True(ring.Reserve(entry));
        Assert.False(ring.Reserve(entry)); // Idempotent same-entry reservation returns false
    }

    [Fact]
    public void TestForwardingRequiresJournalCommit()
    {
        var identity = CreateIdentity();
        var limits = new CustodyLimits(identity.SampleRate, identity.ChannelCount);
        var ring = new CustodyRing(limits);

        var frame = CreateFrame(identity, 1);
        var entry = new CustodyEntry(frame);
        ring.Reserve(entry);

        Assert.Throws<CompanionException>(() => ring.AcknowledgeForwarded(frame.EventId, journalCommitted: false));

        ring.AcknowledgeForwarded(frame.EventId, journalCommitted: true);
        Assert.Equal(CustodyRelease.Forwarded, ring.Released[frame.EventId]);
        Assert.Equal(0, ring.RetainedCount);
    }

    [Fact]
    public void TestLocalDiscardCreatesGapObligation()
    {
        var identity = CreateIdentity();
        var limits = new CustodyLimits(identity.SampleRate, identity.ChannelCount);
        var ring = new CustodyRing(limits);

        var frame = CreateFrame(identity, 1);
        var entry = new CustodyEntry(frame);
        ring.Reserve(entry);

        ring.LocalDiscard(frame.EventId, CustodyRelease.LocalPrivacyDiscard);
        Assert.Equal(CustodyRelease.LocalPrivacyDiscard, ring.Released[frame.EventId]);
        Assert.Equal(CustodyRelease.LocalPrivacyDiscard, ring.GapObligations[frame.EventId]);
    }

    [Fact]
    public void TestClockAdvanceExpiresStaleCustody()
    {
        var identity = CreateIdentity();
        var limits = new CustodyLimits(identity.SampleRate, identity.ChannelCount);
        var ring = new CustodyRing(limits);

        var frame1 = CreateFrame(identity, 1, capturedAtMs: 1000);
        ring.Reserve(new CustodyEntry(frame1));

        var frame2 = CreateFrame(identity, 2, capturedAtMs: 40000);
        ring.Reserve(new CustodyEntry(frame2));

        var expired = ring.AdvanceClock(45000);
        Assert.Contains(frame1.EventId, expired);
        Assert.DoesNotContain(frame2.EventId, expired);
    }

    [Fact]
    public void TestClockMoveBackwardsThrows()
    {
        var identity = CreateIdentity();
        var limits = new CustodyLimits(identity.SampleRate, identity.ChannelCount);
        var ring = new CustodyRing(limits);

        ring.AdvanceClock(5000);
        Assert.Throws<CompanionException>(() => ring.AdvanceClock(4000));
    }
}
