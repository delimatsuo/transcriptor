using System;
using System.Collections.Generic;
using System.Linq;
using TarsNativeCompanion.Contracts;

namespace TarsNativeCompanion.Memory;

public sealed record CustodyLimits
{
    public int SampleRate { get; }
    public int ChannelCount { get; }
    public ulong MaxDurationMs { get; }
    public int MaxFrames { get; }
    public int MaxPayloadBytes { get; }
    public int MaxMetadataBytes { get; }
    public int MaxTerminalRecords { get; }

    public CustodyLimits(int sampleRate, int channelCount)
    {
        if (sampleRate < 8000 || sampleRate > 48000 || channelCount < 1 || channelCount > 2)
        {
            throw new CompanionException("custody format is outside the supported bounds");
        }
        SampleRate = sampleRate;
        ChannelCount = channelCount;
        MaxDurationMs = 2000;
        MaxFrames = Math.Min(96000, sampleRate * 2);
        MaxPayloadBytes = Math.Min(384000, MaxFrames * channelCount * 2);
        MaxMetadataBytes = 409600;
        MaxTerminalRecords = 1024;
    }
}

public enum CustodyRelease
{
    Forwarded,
    DurableDiscard,
    LocalPrivacyDiscard,
    PrivacyTimeout,
    DeletionLocal,
    EmergencyLocal,
    ForwardedAfterLocalRelease,
    AmbiguousEffect
}

public enum ProviderEffectState
{
    Prepared,
    Invoking,
    Terminal,
    Cancelled
}

public sealed record ProviderEffectToken(string EffectId, ulong OwnerGeneration, Guid OwnerEpoch)
{
    public ProviderEffectToken(string effectId, ulong ownerGeneration)
        : this(effectId, ownerGeneration, Guid.NewGuid())
    {
        if (!SourceIdentity.IsIdentifier(effectId))
        {
            throw new CompanionException("effect identifier is invalid");
        }
    }
}

public sealed class ProviderEffectFence
{
    public ProviderEffectToken Token { get; }
    public ProviderEffectState State { get; private set; } = ProviderEffectState.Prepared;
    public bool JournalCommitted { get; private set; }

    public ProviderEffectFence(ProviderEffectToken token)
    {
        Token = token;
    }

    public void MarkInvoking()
    {
        if (State != ProviderEffectState.Prepared) throw new CompanionException("callback is fenced");
        State = ProviderEffectState.Invoking;
    }

    public void MarkTerminal(bool journalCommitted)
    {
        if (State != ProviderEffectState.Invoking) throw new CompanionException("callback is fenced");
        JournalCommitted = journalCommitted;
        State = ProviderEffectState.Terminal;
    }

    public void CancelPrepared()
    {
        if (State != ProviderEffectState.Prepared) throw new CompanionException("callback is fenced");
        State = ProviderEffectState.Cancelled;
    }
}

public enum PendingEffectOutcome
{
    Forwarded,
    Ambiguous
}

public sealed record CustodyEntry
{
    public AudioFrame Frame { get; }
    public int MetadataBytes { get; }

    public CustodyEntry(AudioFrame frame, int metadataBytes = 256)
    {
        if (metadataBytes < 1 || metadataBytes > 4096)
        {
            throw new CompanionException("metadata size is outside the bound");
        }
        Frame = frame;
        MetadataBytes = metadataBytes;
    }

    public int ResidentBytes => Frame.Payload.Count + MetadataBytes;
}

public sealed class CustodyRing
{
    public CustodyLimits Limits { get; }
    public Guid OwnerEpoch { get; }
    private readonly Dictionary<string, CustodyEntry> _entries = new();
    private readonly Dictionary<string, CustodyRelease> _releases = new();
    private readonly Dictionary<string, ProviderEffectFence> _effects = new();
    private readonly HashSet<string> _pendingAfterLocalRelease = new();
    private readonly Dictionary<string, CustodyRelease> _gapReasons = new();

    public bool AcquisitionStopped { get; private set; }
    public ulong LastClockMs { get; private set; }

    public CustodyRing(CustodyLimits limits, Guid? ownerEpoch = null)
    {
        Limits = limits;
        OwnerEpoch = ownerEpoch ?? Guid.NewGuid();
    }

    public int RetainedCount => _entries.Count;
    public int RetainedPayloadBytes => _entries.Values.Sum(e => e.Frame.Payload.Count);
    public int RetainedMetadataBytes => _entries.Values.Sum(e => e.MetadataBytes);
    public ulong RetainedDurationMs => _entries.Values.Aggregate(0UL, (acc, e) => acc + e.Frame.DurationMs);

    public IReadOnlyDictionary<string, CustodyRelease> Released => _releases;
    public IReadOnlySet<string> PendingEffects => _pendingAfterLocalRelease;
    public bool HasPendingProviderEffects =>
        _pendingAfterLocalRelease.Count > 0 ||
        _effects.Values.Any(e => e.State == ProviderEffectState.Prepared ||
                                 e.State == ProviderEffectState.Invoking ||
                                 e.State == ProviderEffectState.Terminal);
    public IReadOnlyDictionary<string, CustodyRelease> GapObligations => _gapReasons;

    public bool Reserve(CustodyEntry entry)
    {
        string eventId = entry.Frame.EventId;
        if (_entries.TryGetValue(eventId, out var existing))
        {
            if (!existing.Equals(entry)) throw new CompanionException("retry changed frame content");
            return false;
        }
        if (_releases.ContainsKey(eventId) || AcquisitionStopped ||
            entry.Frame.Identity.SampleRate != Limits.SampleRate ||
            entry.Frame.Identity.ChannelCount != Limits.ChannelCount)
        {
            throw new CompanionException("frame does not match active custody format");
        }

        int nextCount = _entries.Count + 1;
        int nextPayload = RetainedPayloadBytes + entry.Frame.Payload.Count;
        int nextMetadata = RetainedMetadataBytes + entry.MetadataBytes;
        ulong nextDuration = RetainedDurationMs + entry.Frame.DurationMs;

        if (nextCount > 100 || nextPayload > Limits.MaxPayloadBytes ||
            nextMetadata > Limits.MaxMetadataBytes || nextDuration > Limits.MaxDurationMs)
        {
            AcquisitionStopped = true;
            throw new CompanionException("custody limit exceeded");
        }

        _entries[eventId] = entry;
        LastClockMs = Math.Max(LastClockMs, entry.Frame.CapturedAtMs);
        return true;
    }

    public CustodyEntry? GetEntry(string eventId) =>
        _entries.TryGetValue(eventId, out var e) ? e : null;

    public void RollbackReservation(string eventId)
    {
        if (!_entries.Remove(eventId, out var entry) || _releases.ContainsKey(eventId))
        {
            throw new CompanionException("reservation rollback is stale or already released");
        }
        entry.Frame.Payload.Zeroize();
    }

    public void PrepareEffect(string eventId, ProviderEffectToken token)
    {
        if (!_entries.TryGetValue(eventId, out var entry) ||
            _releases.ContainsKey(eventId) ||
            _effects.ContainsKey(eventId) ||
            _pendingAfterLocalRelease.Contains(eventId) ||
            _effects.Count >= Limits.MaxTerminalRecords ||
            token.OwnerGeneration != entry.Frame.Identity.CaptureGeneration ||
            token.OwnerEpoch != OwnerEpoch)
        {
            throw new CompanionException("provider effect requires live unreleased custody");
        }
        _effects[eventId] = new ProviderEffectFence(token);
    }

    public ProviderEffectFence? GetEffect(string eventId) =>
        _effects.TryGetValue(eventId, out var f) ? f : null;

    public void MarkEffectInvoking(string eventId, ProviderEffectToken token)
    {
        if (_pendingAfterLocalRelease.Contains(eventId) ||
            !_effects.TryGetValue(eventId, out var effect) ||
            effect.Token != token)
        {
            throw new CompanionException("callback is fenced");
        }
        effect.MarkInvoking();
    }

    public void MarkEffectTerminal(string eventId, ProviderEffectToken token, bool journalCommitted)
    {
        if (!_effects.TryGetValue(eventId, out var effect) || effect.Token != token)
        {
            throw new CompanionException("callback is fenced");
        }
        effect.MarkTerminal(journalCommitted);
    }

    public void AcknowledgeForwarded(string eventId, bool journalCommitted)
    {
        if (_effects.ContainsKey(eventId))
        {
            throw new CompanionException("effect-bound forwarding requires its fence");
        }
        if (!journalCommitted)
        {
            throw new CompanionException("forwarding requires immutable journal acknowledgement");
        }
        Release(eventId, CustodyRelease.Forwarded);
    }

    public void AcknowledgeEffectForwarded(string eventId, ProviderEffectToken token)
    {
        if (!_effects.TryGetValue(eventId, out var effect) ||
            effect.Token != token ||
            effect.State != ProviderEffectState.Terminal ||
            !effect.JournalCommitted ||
            _pendingAfterLocalRelease.Contains(eventId))
        {
            throw new CompanionException("callback is fenced");
        }
        Release(eventId, CustodyRelease.Forwarded);
        _effects.Remove(eventId);
    }

    public void LocalDiscard(string eventId, CustodyRelease reason)
    {
        if (reason != CustodyRelease.LocalPrivacyDiscard &&
            reason != CustodyRelease.PrivacyTimeout &&
            reason != CustodyRelease.DeletionLocal &&
            reason != CustodyRelease.EmergencyLocal ||
            _releases.ContainsKey(eventId))
        {
            throw new CompanionException("local discard reason or release state is invalid");
        }

        if (_effects.TryGetValue(eventId, out var effect))
        {
            if (effect.State == ProviderEffectState.Terminal)
            {
                throw new CompanionException("callback is fenced");
            }
            if (effect.State == ProviderEffectState.Prepared)
            {
                effect.CancelPrepared();
            }
        }

        bool hasInvokingEffect = _effects.TryGetValue(eventId, out var eff) && eff.State == ProviderEffectState.Invoking;
        if (!hasInvokingEffect)
        {
            EnsureTerminalCapacity(eventId);
            EnsureGapCapacity(eventId);
        }

        Release(eventId, reason);
        if (hasInvokingEffect)
        {
            _pendingAfterLocalRelease.Add(eventId);
        }
        else
        {
            RecordGapObligation(eventId, reason);
        }
    }

    public void CancelPreparedEffectAndDiscard(
        string eventId,
        ProviderEffectToken token,
        CustodyRelease gapReason = CustodyRelease.DurableDiscard)
    {
        if (!_effects.TryGetValue(eventId, out var effect) ||
            effect.Token != token ||
            effect.State != ProviderEffectState.Prepared ||
            _pendingAfterLocalRelease.Contains(eventId) ||
            gapReason != CustodyRelease.DurableDiscard)
        {
            throw new CompanionException("callback is fenced");
        }
        effect.CancelPrepared();
        EnsureTerminalCapacity(eventId);
        EnsureGapCapacity(eventId);
        Release(eventId, gapReason);
        RecordGapObligation(eventId, gapReason);
    }

    public void ResolvePendingEffect(string eventId, ProviderEffectToken token, PendingEffectOutcome outcome)
    {
        if (!_pendingAfterLocalRelease.Contains(eventId) ||
            !_effects.TryGetValue(eventId, out var effect) ||
            effect.Token != token ||
            effect.State != ProviderEffectState.Terminal)
        {
            throw new CompanionException("callback is fenced");
        }

        switch (outcome)
        {
            case PendingEffectOutcome.Forwarded:
                if (!effect.JournalCommitted) throw new CompanionException("forwarded resolution requires journal");
                _releases[eventId] = CustodyRelease.ForwardedAfterLocalRelease;
                break;
            case PendingEffectOutcome.Ambiguous:
                if (effect.JournalCommitted) throw new CompanionException("ambiguous effect has a committed journal");
                RecordGapObligation(eventId, CustodyRelease.AmbiguousEffect);
                break;
        }

        _pendingAfterLocalRelease.Remove(eventId);
        _effects.Remove(eventId);
    }

    public void ResolveEffectAmbiguous(string eventId, ProviderEffectToken token)
    {
        if (!_effects.TryGetValue(eventId, out var effect) ||
            effect.Token != token ||
            effect.State != ProviderEffectState.Terminal ||
            effect.JournalCommitted ||
            _pendingAfterLocalRelease.Contains(eventId))
        {
            throw new CompanionException("callback is fenced");
        }

        EnsureTerminalCapacity(eventId);
        EnsureGapCapacity(eventId);
        Release(eventId, CustodyRelease.AmbiguousEffect);
        RecordGapObligation(eventId, CustodyRelease.AmbiguousEffect);
        _effects.Remove(eventId);
    }

    public List<string> AdvanceClock(ulong nowMs, bool clockCertain = true)
    {
        if (nowMs < LastClockMs) throw new CompanionException("clock moved backwards");
        LastClockMs = nowMs;

        if (!clockCertain)
        {
            AcquisitionStopped = true;
            var all = _entries.Keys.OrderBy(k => k).ToList();
            foreach (var id in all) LocalDiscard(id, CustodyRelease.PrivacyTimeout);
            return all;
        }

        if (_entries.Values.Any(e => nowMs - e.Frame.CapturedAtMs >= 10000))
        {
            AcquisitionStopped = true;
        }

        var expired = _entries.Values
            .Where(e => nowMs - e.Frame.CapturedAtMs >= 30000)
            .Select(e => e.Frame.EventId)
            .OrderBy(k => k)
            .ToList();

        foreach (var id in expired) LocalDiscard(id, CustodyRelease.PrivacyTimeout);
        return expired;
    }

    private void Release(string eventId, CustodyRelease reason)
    {
        EnsureTerminalCapacity(eventId);
        if (!_entries.Remove(eventId, out var entry))
        {
            if (!_releases.TryGetValue(eventId, out var existing) || existing != reason)
            {
                throw new CompanionException("release references missing or conflicting custody");
            }
            return;
        }
        entry.Frame.Payload.Zeroize();
        _releases[eventId] = reason;
    }

    private void EnsureTerminalCapacity(string eventId)
    {
        if (!_releases.ContainsKey(eventId) && _releases.Count >= Limits.MaxTerminalRecords)
        {
            throw new CompanionException("custody limit exceeded");
        }
    }

    private void RecordGapObligation(string eventId, CustodyRelease reason)
    {
        EnsureGapCapacity(eventId);
        _gapReasons[eventId] = reason;
    }

    private void EnsureGapCapacity(string eventId)
    {
        if (!_gapReasons.ContainsKey(eventId) && _gapReasons.Count >= Limits.MaxTerminalRecords)
        {
            throw new CompanionException("custody limit exceeded");
        }
    }
}
