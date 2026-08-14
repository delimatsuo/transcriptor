using System.Buffers.Binary;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

// Pure, dependency-free v2 vector runner. It intentionally contains no
// networking, device, provider, filesystem, or credential integration.
internal static class Program
{
    private readonly record struct StreamKey(
        string SessionId,
        string StreamId,
        ulong CaptureGeneration,
        string Source);

    private readonly record struct Atomic(
        StreamKey Key,
        ulong Sequence,
        ulong FirstSample,
        ulong LastSampleExclusive)
    {
        internal string Id => AtomicId(this);
    }

    private readonly record struct AudioFrameInput(
        StreamKey Key,
        ulong Sequence,
        ulong FirstSample,
        ulong LastSampleExclusive,
        int SampleRateHertz,
        int ChannelCount,
        int DurationMs,
        byte[] Payload);

    private readonly record struct ParsedAudioFrame(
        AudioFrameInput Input,
        string EventId,
        byte[] CanonicalMetadata);

    private sealed class RetryLedger
    {
        private readonly string sessionId;
        private readonly byte[] sessionKey;
        private readonly Dictionary<string, byte[]> commitments;

        internal RetryLedger(
            string sessionId,
            byte[] sessionKey,
            IReadOnlyDictionary<string, byte[]>? snapshot = null)
        {
            ValidateIdentifier("sessionId", sessionId);
            if (sessionKey.Length < 32)
                throw new InvalidOperationException("session retry key is too short");
            this.sessionId = sessionId;
            this.sessionKey = sessionKey.ToArray();
            commitments = snapshot?.ToDictionary(
                pair => pair.Key,
                pair => pair.Value.ToArray(),
                StringComparer.Ordinal) ?? new Dictionary<string, byte[]>(StringComparer.Ordinal);
            foreach ((string eventId, byte[] commitment) in commitments)
            {
                ValidateIdentifier("eventId", eventId);
                if (commitment.Length != 32)
                    throw new InvalidOperationException("stored retry commitment is invalid");
            }
        }

        internal bool Admit(byte[] frame)
        {
            ParsedAudioFrame parsed = ParseAudioFrame(frame);
            if (parsed.Input.Key.SessionId != sessionId)
                throw new InvalidOperationException("retry commitment session mismatch");
            byte[] commitment = RetryCommitment(sessionKey, parsed.CanonicalMetadata, parsed.Input.Payload);
            if (commitments.TryGetValue(parsed.EventId, out byte[]? existing))
            {
                if (!CryptographicOperations.FixedTimeEquals(existing, commitment))
                    throw new InvalidOperationException("retry identity was reused with changed content");
                return false;
            }
            commitments.Add(parsed.EventId, commitment);
            return true;
        }

        internal IReadOnlyDictionary<string, byte[]> Snapshot() => commitments.ToDictionary(
            pair => pair.Key,
            pair => pair.Value.ToArray(),
            StringComparer.Ordinal);
    }

    private sealed class QuotaBucket
    {
        private readonly long eventRate;
        private readonly long eventBurst;
        private readonly long payloadRate;
        private readonly long payloadBurst;
        private readonly long metadataRate;
        private readonly long metadataBurst;
        private readonly long custodyLimit;
        private long events;
        private long payload;
        private long metadata;
        private long custody;
        private long lastSecond;

        internal QuotaBucket(
            long eventRate,
            long eventBurst,
            long payloadRate,
            long payloadBurst,
            long metadataRate,
            long metadataBurst,
            long custodyLimit)
        {
            long[] values = { eventRate, eventBurst, payloadRate, payloadBurst, metadataRate, metadataBurst, custodyLimit };
            if (values.Any(value => value < 0))
                throw new InvalidOperationException("quota limit is negative");
            this.eventRate = eventRate;
            this.eventBurst = eventBurst;
            this.payloadRate = payloadRate;
            this.payloadBurst = payloadBurst;
            this.metadataRate = metadataRate;
            this.metadataBurst = metadataBurst;
            this.custodyLimit = custodyLimit;
            events = eventBurst;
            payload = payloadBurst;
            metadata = metadataBurst;
        }

        internal bool Reserve(long second, long requestedEvents, long payloadBytes, long metadataBytes, long custodyBytes)
        {
            if (second < lastSecond || new[] { requestedEvents, payloadBytes, metadataBytes, custodyBytes }.Any(value => value < 0))
                throw new InvalidOperationException("quota reservation is invalid");
            long elapsed = second - lastSecond;
            if (elapsed > 0)
            {
                events = Math.Min(eventBurst, checked(events + elapsed * eventRate));
                payload = Math.Min(payloadBurst, checked(payload + elapsed * payloadRate));
                metadata = Math.Min(metadataBurst, checked(metadata + elapsed * metadataRate));
                lastSecond = second;
            }
            bool allowed = events >= requestedEvents && payload >= payloadBytes && metadata >= metadataBytes &&
                           custodyBytes <= custodyLimit - custody;
            events = Math.Max(0, events - requestedEvents);
            payload = Math.Max(0, payload - payloadBytes);
            metadata = Math.Max(0, metadata - metadataBytes);
            if (allowed) custody += custodyBytes;
            return allowed;
        }

        internal void Release(long bytes)
        {
            if (bytes < 0 || bytes > custody)
                throw new InvalidOperationException("quota custody release exceeds reservation");
            custody -= bytes;
        }

        internal long Custody => custody;
    }

    private readonly record struct EffectToken(
        string EffectId,
        ulong RuntimeEpoch,
        ulong EgressFence,
        string OwnerId);

    private sealed class EffectFence
    {
        private readonly string effectId;
        private string state = "prepared";
        private ulong runtimeEpoch;
        private ulong egressFence;
        private string? ownerId;
        private EffectToken? token;
        private bool providerClosed;
        private bool ownerTerminated;

        internal EffectFence(string effectId)
        {
            if (string.IsNullOrEmpty(effectId))
                throw new InvalidOperationException("effect identity is required");
            this.effectId = effectId;
        }

        internal int InvokeCount { get; private set; }
        internal string EffectId => effectId;
        internal string? OwnerId => ownerId;
        internal bool JournalCommitted { get; private set; }
        internal string State => state;
        internal bool IsPreparedOwned => state == "prepared" && ownerId is not null && token.HasValue;
        internal bool CancelledWithoutInvoke { get; private set; }

        internal EffectToken Prepare(string owner)
        {
            if (state != "prepared" || string.IsNullOrEmpty(owner))
                throw new InvalidOperationException("effect is not prepareable");
            if (ownerId is not null)
            {
                if (ownerId == owner && token.HasValue) return token.Value;
                throw new InvalidOperationException("effect already has a durable owner");
            }
            ownerId = owner;
            token = new EffectToken(effectId, runtimeEpoch, egressFence, owner);
            return token.Value;
        }

        internal void Invoke(EffectToken presented)
        {
            Check(presented);
            if (state != "prepared")
                throw new InvalidOperationException("effect invocation is not single-use");
            state = "invoking";
            InvokeCount++;
        }

        internal void CancelPrepared(EffectToken presented)
        {
            Check(presented);
            if (state != "prepared" || InvokeCount != 0 || JournalCommitted)
                throw new InvalidOperationException("only an uninvoked prepared effect can be cancelled");
            CancelledWithoutInvoke = true;
            state = "terminal";
        }

        internal void Callback(EffectToken presented)
        {
            Check(presented);
            if (state is not ("invoking" or "provider_returned" or "journaled"))
                throw new InvalidOperationException("provider callback is late or out of order");
        }

        internal void ProviderReturned(EffectToken presented)
        {
            Check(presented);
            if (state != "invoking")
                throw new InvalidOperationException("provider return is out of order");
            state = "provider_returned";
        }

        internal void CommitJournal(EffectToken presented)
        {
            Check(presented);
            if (state != "provider_returned")
                throw new InvalidOperationException("journal is out of order");
            JournalCommitted = true;
            state = "journaled";
        }

        internal void Recover(ulong epoch, ulong fence)
        {
            if (state == "terminal" || epoch <= runtimeEpoch || fence <= egressFence)
                throw new InvalidOperationException("recovery epoch or fence is invalid");
            runtimeEpoch = epoch;
            egressFence = fence;
            providerClosed = false;
            ownerTerminated = false;
            state = "effect_quiescence_required";
        }

        internal void AcknowledgeProviderClose()
        {
            if (state != "effect_quiescence_required")
                throw new InvalidOperationException("provider close must acknowledge the current recovery fence");
            providerClosed = true;
        }

        internal void AcknowledgeOwnerTermination()
        {
            if (state != "effect_quiescence_required")
                throw new InvalidOperationException("owner termination must acknowledge the current recovery fence");
            ownerTerminated = true;
        }

        internal void Terminalize()
        {
            if (state != "effect_quiescence_required" || !providerClosed || !ownerTerminated)
                throw new InvalidOperationException("positive effect quiescence is required");
            state = "terminal";
        }

        private void Check(EffectToken presented)
        {
            if (!token.HasValue || presented != token.Value || presented.EffectId != effectId ||
                presented.RuntimeEpoch != runtimeEpoch || presented.EgressFence != egressFence)
            {
                throw new InvalidOperationException("effect token is stale or foreign");
            }
        }
    }

    private readonly record struct CustodyItem(
        long Frames,
        int PayloadBytes,
        int MetadataBytes,
        int ResidentBytes,
        long CapturedAtMs);

    private sealed class CustodyBudget
    {
        private readonly int sampleRate;
        private readonly int channels;
        private readonly Dictionary<string, CustodyItem> items = new(StringComparer.Ordinal);
        private readonly Dictionary<string, string> released = new(StringComparer.Ordinal);
        private readonly Dictionary<string, string> gapIds = new(StringComparer.Ordinal);
        private readonly Dictionary<string, EffectFence> effects = new(StringComparer.Ordinal);
        private readonly HashSet<string> pendingEffectReleases = new(StringComparer.Ordinal);
        private readonly HashSet<string> forwarded = new(StringComparer.Ordinal);
        private long lastClockMs;

        internal CustodyBudget(int sampleRate, int channels)
        {
            if (sampleRate is < 8_000 or > 48_000 || channels is < 1 or > 2)
                throw new InvalidOperationException("custody format is invalid");
            this.sampleRate = sampleRate;
            this.channels = channels;
        }

        internal int Count => items.Count;
        internal long RetainedFrames => items.Values.Sum(value => value.Frames);
        internal long RetainedPayloadBytes => items.Values.Sum(value => (long)value.PayloadBytes);
        internal long RetainedMetadataBytes => items.Values.Sum(value => (long)value.MetadataBytes);
        internal long RetainedResidentBytes => items.Values.Sum(value => (long)value.ResidentBytes);
        internal bool AcquisitionStopped { get; private set; }

        internal bool Reserve(string eventId, CustodyItem item)
        {
            if (items.TryGetValue(eventId, out CustodyItem existing))
            {
                if (existing == item) return false;
                throw new InvalidOperationException("custody retry changed content");
            }
            if (released.ContainsKey(eventId) || AcquisitionStopped || string.IsNullOrEmpty(eventId) ||
                item.Frames <= 0 || item.PayloadBytes <= 0 || item.PayloadBytes > 64_000 ||
                item.MetadataBytes is < 1 or > 4_096 || item.ResidentBytes < 0 || item.CapturedAtMs < lastClockMs ||
                item.Frames > long.MaxValue / (channels * 2L) || item.Frames * channels * 2L != item.PayloadBytes ||
                item.Frames > long.MaxValue / 1_000 || item.Frames * 1_000 % sampleRate != 0 ||
                item.Frames * 1_000 / sampleRate is < 20 or > 250 ||
                item.ResidentBytes < (long)item.PayloadBytes + item.MetadataBytes)
            {
                throw new InvalidOperationException("custody reservation is outside framing bounds");
            }
            long maxFrames = Math.Min(96_000, 2L * sampleRate);
            long maxPayload = Math.Min(384_000, maxFrames * channels * 2L);
            if (items.Count + 1 > 100 || RetainedFrames + item.Frames > maxFrames ||
                RetainedPayloadBytes + item.PayloadBytes > maxPayload ||
                RetainedMetadataBytes + item.MetadataBytes > 409_600 ||
                RetainedResidentBytes + item.ResidentBytes > 1_048_576)
            {
                AcquisitionStopped = true;
                throw new InvalidOperationException("custody aggregate bound exceeded");
            }
            items.Add(eventId, item);
            lastClockMs = item.CapturedAtMs;
            return true;
        }

        internal void Forward(string eventId, bool journalCommitted)
        {
            if (effects.ContainsKey(eventId))
                throw new InvalidOperationException("registered provider effect requires effect-bound forwarding");
            if (!journalCommitted)
                throw new InvalidOperationException("forwarding release requires journal");
            Release(eventId, "forwarded");
            forwarded.Add(eventId);
        }

        internal void ForwardEffect(string eventId, EffectFence effect)
        {
            if (!effects.TryGetValue(eventId, out EffectFence? registered) ||
                !ReferenceEquals(registered, effect) || !effect.JournalCommitted)
                throw new InvalidOperationException("effect-bound forwarding requires the original immutable journal");
            if (pendingEffectReleases.Contains(eventId))
                throw new InvalidOperationException("locally released custody requires pending-effect resolution");
            Release(eventId, "forwarded");
            forwarded.Add(eventId);
        }

        internal void Discard(string eventId, string gapId)
        {
            if (string.IsNullOrEmpty(gapId) || forwarded.Contains(eventId) || effects.ContainsKey(eventId) ||
                pendingEffectReleases.Contains(eventId))
                throw new InvalidOperationException("discard conflicts with forwarding");
            if (gapIds.TryGetValue(eventId, out string? existing) && existing != gapId)
                throw new InvalidOperationException("discard identity replay conflicts");
            Release(eventId, "durable_discard");
            gapIds[eventId] = gapId;
        }

        internal void CancelPreparedEffectAndDiscard(string eventId, EffectFence effect, string gapId)
        {
            if (released.GetValueOrDefault(eventId) == "durable_discard" &&
                gapIds.GetValueOrDefault(eventId) == gapId && effect.CancelledWithoutInvoke)
            {
                return;
            }
            if (string.IsNullOrEmpty(gapId) || !items.ContainsKey(eventId) ||
                !effects.TryGetValue(eventId, out EffectFence? registered) || !ReferenceEquals(registered, effect) ||
                pendingEffectReleases.Contains(eventId))
            {
                throw new InvalidOperationException("prepared-effect discard is stale, active, or foreign");
            }
            EffectToken token = effect.Prepare(effect.OwnerId!);
            effect.CancelPrepared(token);
            Release(eventId, "durable_discard");
            gapIds[eventId] = gapId;
        }

        internal void RegisterEffect(string eventId, EffectFence effect)
        {
            if (!items.ContainsKey(eventId) || released.ContainsKey(eventId) || gapIds.ContainsKey(eventId) ||
                !effect.IsPreparedOwned)
            {
                throw new InvalidOperationException("provider effect requires live unreleased custody and a durable owner");
            }
            if (effects.TryGetValue(eventId, out EffectFence? existing))
            {
                if (ReferenceEquals(existing, effect)) return;
                throw new InvalidOperationException("range already has a different provider effect");
            }
            effects[eventId] = effect;
        }

        internal void InvokeEffect(string eventId, EffectFence effect, EffectToken token)
        {
            if (!items.ContainsKey(eventId) || !effects.TryGetValue(eventId, out EffectFence? registered) ||
                !ReferenceEquals(registered, effect))
                throw new InvalidOperationException("provider invocation requires registered live custody");
            effect.Invoke(token);
        }

        internal void LocalPrivacyRelease(string eventId, string reason)
        {
            if (reason is not ("privacy_timeout_local" or "deletion_local" or "emergency_local") ||
                forwarded.Contains(eventId))
            {
                throw new InvalidOperationException("local privacy release is invalid");
            }
            Release(eventId, reason);
            if (effects.ContainsKey(eventId)) pendingEffectReleases.Add(eventId);
            else gapIds[eventId] = reason;
        }

        internal void ResolvePendingEffect(string eventId, EffectFence effect, string outcome)
        {
            if (!pendingEffectReleases.Contains(eventId) ||
                !effects.TryGetValue(eventId, out EffectFence? registered) || !ReferenceEquals(registered, effect))
                throw new InvalidOperationException("pending effect resolution is stale or foreign");
            if (outcome == "forwarded")
            {
                if (!effect.JournalCommitted)
                    throw new InvalidOperationException("forwarded resolution requires immutable journal");
                forwarded.Add(eventId);
                released[eventId] = "forwarded_after_local_release";
            }
            else if (outcome == "ambiguous_effect")
            {
                if (effect.State != "terminal" || effect.JournalCommitted)
                    throw new InvalidOperationException("ambiguous resolution requires unforwarded positive quiescence");
                gapIds[eventId] = "ambiguous_effect";
            }
            else
            {
                throw new InvalidOperationException("pending effect cannot resolve as discard");
            }
            pendingEffectReleases.Remove(eventId);
        }

        internal bool IsPendingEffectRelease(string eventId) => pendingEffectReleases.Contains(eventId);
        internal bool IsForwarded(string eventId) => forwarded.Contains(eventId);
        internal string? GapFor(string eventId) => gapIds.GetValueOrDefault(eventId);

        internal IReadOnlyList<string> Advance(long nowMs, bool clockCertain)
        {
            if (nowMs < lastClockMs)
                throw new InvalidOperationException("custody clock moved backwards");
            lastClockMs = nowMs;
            if (!clockCertain) AcquisitionStopped = true;
            if (items.Values.Any(value => nowMs - value.CapturedAtMs >= 10_000)) AcquisitionStopped = true;
            string[] expired = items
                .Where(pair => !clockCertain || nowMs - pair.Value.CapturedAtMs >= 30_000)
                .Select(pair => pair.Key)
                .OrderBy(value => value, StringComparer.Ordinal)
                .ToArray();
            foreach (string eventId in expired) LocalPrivacyRelease(eventId, "privacy_timeout_local");
            return expired;
        }

        private void Release(string eventId, string outcome)
        {
            if (!items.Remove(eventId))
            {
                if (released.GetValueOrDefault(eventId) == outcome) return;
                throw new InvalidOperationException("release references absent or conflicting custody");
            }
            released[eventId] = outcome;
        }
    }

    private sealed class DeletionFence
    {
        private readonly HashSet<string> participants;
        private readonly HashSet<string> stores;
        private readonly HashSet<string> acknowledgements = new(StringComparer.Ordinal);
        private readonly Dictionary<int, Dictionary<string, bool>> passes = new();

        internal DeletionFence(IEnumerable<string> participants, IEnumerable<string> stores)
        {
            this.participants = new HashSet<string>(participants, StringComparer.Ordinal);
            this.stores = new HashSet<string>(stores, StringComparer.Ordinal);
            if (this.participants.Any(string.IsNullOrEmpty) || this.stores.Any(string.IsNullOrEmpty))
                throw new InvalidOperationException("deletion identity is invalid");
        }

        internal string State { get; private set; } = "active";
        internal ulong Generation { get; private set; }
        internal int LateCallbacks { get; private set; }

        internal ulong Request()
        {
            if (State != "active" || Generation == ulong.MaxValue)
                throw new InvalidOperationException("deletion request is out of order");
            Generation++;
            State = "delete_quiescing";
            return Generation;
        }

        internal void Acknowledge(string participant, ulong generation)
        {
            if (State != "delete_quiescing" || generation != Generation || !participants.Contains(participant))
                throw new InvalidOperationException("deletion acknowledgement is stale or foreign");
            acknowledgements.Add(participant);
        }

        internal void StartDeleting()
        {
            if (State != "delete_quiescing" || !acknowledgements.SetEquals(participants))
                throw new InvalidOperationException("positive deletion quiescence is required");
            State = "deleting";
        }

        internal bool RecordPass(int number, IReadOnlyDictionary<string, bool> results)
        {
            if (State != "deleting" || number is < 1 or > 2 || number == 2 && !passes.ContainsKey(1) ||
                !stores.SetEquals(results.Keys))
            {
                throw new InvalidOperationException("absence pass is out of order or incomplete");
            }
            var normalized = new Dictionary<string, bool>(results, StringComparer.Ordinal);
            if (passes.TryGetValue(number, out Dictionary<string, bool>? existing))
            {
                if (existing.Count != normalized.Count || existing.Any(pair => !normalized.TryGetValue(pair.Key, out bool value) || value != pair.Value))
                    throw new InvalidOperationException("absence pass replay conflicts");
                return true;
            }
            if (normalized.Values.Any(value => !value))
            {
                State = "deletion_failed";
                return false;
            }
            passes.Add(number, normalized);
            return true;
        }

        internal void Resume(ulong generation)
        {
            if (State != "deletion_failed" || generation != Generation)
                throw new InvalidOperationException("deletion resume is stale");
            State = "deleting";
        }

        internal void RejectLateCallback(ulong generation)
        {
            if (State == "active" || generation > Generation)
                throw new InvalidOperationException("callback generation is not fenced");
            LateCallbacks++;
            throw new InvalidOperationException("late callback rejected before persistence");
        }

        internal void Finish()
        {
            if (State != "deleting" || !passes.ContainsKey(1) || !passes.ContainsKey(2))
                throw new InvalidOperationException("two absence passes are required");
            State = "deleted";
        }
    }

    private readonly record struct PendingConnection(string SourceIp, long StartedAtMs, int ReceiveBytes);

    private sealed class TransportEdgeBudget
    {
        private readonly Dictionary<string, PendingConnection> pending = new(StringComparer.Ordinal);
        private readonly HashSet<string> authenticated = new(StringComparer.Ordinal);

        internal long PendingBytes => pending.Values.Sum(value => (long)value.ReceiveBytes);
        internal int ParserBytes => checked(authenticated.Count * 68_100);

        internal void Open(
            string connectionId,
            string sourceIp,
            long nowMs,
            int headerBytes,
            int firstAuthBytes,
            int receiveBytes)
        {
            if (pending.ContainsKey(connectionId) || authenticated.Contains(connectionId) ||
                string.IsNullOrEmpty(connectionId) || string.IsNullOrEmpty(sourceIp) || nowMs < 0 ||
                headerBytes is < 0 or > 16_384 || firstAuthBytes is < 0 or > 8_192 ||
                receiveBytes is < 0 or > 32_768 || pending.Count >= 64 ||
                pending.Values.Count(value => value.SourceIp == sourceIp) >= 16 ||
                PendingBytes + receiveBytes > 2_097_152)
            {
                throw new InvalidOperationException("pending transport bound exceeded");
            }
            pending.Add(connectionId, new PendingConnection(sourceIp, nowMs, receiveBytes));
        }

        internal void RejectPreAuthAudio(int declaredBytes)
        {
            if (declaredBytes < 0)
                throw new InvalidOperationException("declared audio length is invalid");
            throw new InvalidOperationException("audio is rejected before authentication");
        }

        internal void Authenticate(string connectionId, long nowMs)
        {
            if (!pending.TryGetValue(connectionId, out PendingConnection value) || nowMs < value.StartedAtMs ||
                nowMs - value.StartedAtMs > 8_000 || authenticated.Count >= 16)
            {
                throw new InvalidOperationException("authentication deadline or connection bound exceeded");
            }
            pending.Remove(connectionId);
            authenticated.Add(connectionId);
        }
    }

    private static void Main()
    {
        var key = new StreamKey("session-v2", "stream-mic", 4, "microphone");
        var first = new Atomic(key, 0, 0, 160);
        var second = new Atomic(key, 1, 160, 320);
        const string expectedFirst = "acov_9646759fd911e57a6aa8eceb7101c1b86107b24b53fa0db200beb351b8ed6923";
        const string expectedSecond = "acov_7253b33653a2042851ea98d4b59a302b62594fba658f19925fd16c6646c90895";
        const string expectedTerminal = "covr_b501309bf531e3b7dc293857fc50752387fa7de3b48650820fff33d4024bb939";
        const string expectedSegment = "seg_5bff65fae2a94b2ed887183957588ed3d650bcf1c87696f9d089f5a95282a50f";

        if (first.Id != expectedFirst ||
            second.Id != expectedSecond ||
            TerminalId(key, new[] { second, first }) != expectedTerminal ||
            SegmentId(key, new[] { first }, 20, 120, 0, "fixture", "result-1", 2) != expectedSegment)
        {
            throw new InvalidOperationException("protocol-v2 vector mismatch");
        }

        var overlap = new Atomic(key, 1, 80, 240);
        var middle = new Atomic(key, 1, 800, 960);
        var nonadjacentOverlap = new Atomic(key, 2, 80, 120);
        ExpectReject(() => TerminalId(key, new[] { first, first }));
        ExpectReject(() => TerminalId(key, new[] { first, overlap }));
        ExpectReject(() => TerminalId(key, new[] { first, middle, nonadjacentOverlap }));
        ExpectReject(() => SegmentId(key, new[] { first }, 10, 10, 0, "fixture", "result", null));
        ExpectReject(() => SegmentId(key, new[] { first }, 0, 1, 0, "fixture", "result-e\u0301", null));
        ExpectReject(() => AtomicId(new Atomic(key, 2, 4, 4)));
        ExpectReject(() => AtomicId(new Atomic(
            new StreamKey("session\0bad", "stream-mic", 4, "microphone"), 2, 0, 1)));

        byte[] payload = Enumerable.Range(0, 320)
            .Select(index => (byte)((index * 17 + 3) % 256))
            .ToArray();
        var audio = new AudioFrameInput(key, 0, 0, 160, 8_000, 1, 20, payload);
        byte[] metadata = CanonicalAudioMetadata(audio);
        byte[] frame = EncodeAudioFrame(audio);
        ParsedAudioFrame parsed = ParseAudioFrame(frame);
        if (parsed.EventId != "aevt_93876bd7ae88af5c4c875e668bae680ce508d9982fc7f0f8d8e009c234f6dca2" ||
            parsed.Input.Key != audio.Key || parsed.Input.Sequence != audio.Sequence ||
            parsed.Input.FirstSample != audio.FirstSample ||
            parsed.Input.LastSampleExclusive != audio.LastSampleExclusive ||
            parsed.Input.SampleRateHertz != audio.SampleRateHertz ||
            parsed.Input.ChannelCount != audio.ChannelCount || parsed.Input.DurationMs != audio.DurationMs ||
            !parsed.Input.Payload.SequenceEqual(payload) ||
            metadata.Length != 472 || Hex(Sha256(metadata)) != "4d4bfb8c38171b661d1a3890059701bbd343a4d6e2cfc62c1ff045cc8e1858bd" ||
            frame.Length != 796 || Hex(Sha256(frame)) != "b6a1f52fe0d0bf30ab444c16ec5c9c935c014109fa4d38d06a6ca782866a23ed")
        {
            throw new InvalidOperationException("canonical audio frame vector mismatch");
        }

        byte[] retryKey = Enumerable.Range(0, 32).Select(index => (byte)index).ToArray();
        byte[] commitment = RetryCommitment(retryKey, metadata, payload);
        if (Hex(commitment) != "4a8d1b9605f776c966ac0d62c5a459ead0922a026c521f9e95accce7f069e4c2")
            throw new InvalidOperationException("retry commitment vector mismatch");
        var ledger = new RetryLedger("session-v2", retryKey);
        if (!ledger.Admit(frame) || ledger.Admit(frame))
            throw new InvalidOperationException("retry ledger idempotency mismatch");
        var restartedLedger = new RetryLedger("session-v2", retryKey, ledger.Snapshot());
        if (restartedLedger.Admit(frame))
            throw new InvalidOperationException("retry ledger restart mismatch");

        byte[] changedPayload = payload.Select(value => (byte)(value ^ 0x5a)).ToArray();
        var changedAudio = audio with { Payload = changedPayload };
        ExpectReject(() => restartedLedger.Admit(EncodeAudioFrame(changedAudio)));
        ExpectReject(() => ParseAudioFrame(frame[..^1]));
        byte[] changedFrame = frame.ToArray();
        changedFrame[^1] ^= 1;
        ExpectReject(() => ParseAudioFrame(changedFrame));
        ExpectReject(() => ParseAudioFrame(new byte[] { 0, 0, 16, 1, (byte)'{', (byte)'}' }));
        byte[] noncanonicalMetadata = Encoding.UTF8.GetBytes("{ " + Encoding.UTF8.GetString(metadata)[1..]);
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(noncanonicalMetadata, payload)));
        byte[] wrongIdentityMetadata = Encoding.UTF8.GetBytes(
            Encoding.UTF8.GetString(metadata).Replace(parsed.EventId, "aevt_" + new string('0', 64), StringComparison.Ordinal));
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(wrongIdentityMetadata, payload)));
        byte[] leadingZeroMetadata = Encoding.UTF8.GetBytes(
            Encoding.UTF8.GetString(metadata).Replace("\"sequence\":\"0\"", "\"sequence\":\"00\"", StringComparison.Ordinal));
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(leadingZeroMetadata, payload)));
        byte[] extraFieldMetadata = Encoding.UTF8.GetBytes(
            Encoding.UTF8.GetString(metadata)[..^1] + ",\"unexpected\":true}");
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(extraFieldMetadata, payload)));

        byte[] duplicateFieldMetadata = Encoding.UTF8.GetBytes(
            Encoding.UTF8.GetString(metadata)[..^1] + ",\"sequence\":\"0\"}");
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(duplicateFieldMetadata, payload)));
        byte[] exponentMetadata = Encoding.UTF8.GetBytes(
            Encoding.UTF8.GetString(metadata).Replace("\"protocolVersion\":2", "\"protocolVersion\":2e0", StringComparison.Ordinal));
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(exponentMetadata, payload)));
        byte[] negativeMetadata = Encoding.UTF8.GetBytes(
            Encoding.UTF8.GetString(metadata).Replace("\"durationMs\":20", "\"durationMs\":-20", StringComparison.Ordinal));
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(negativeMetadata, payload)));
        byte[] booleanMetadata = Encoding.UTF8.GetBytes(
            Encoding.UTF8.GetString(metadata).Replace("\"channelCount\":1", "\"channelCount\":true", StringComparison.Ordinal));
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(booleanMetadata, payload)));
        byte[] overflowMetadata = Encoding.UTF8.GetBytes(
            Encoding.UTF8.GetString(metadata).Replace("\"captureGeneration\":\"4\"", "\"captureGeneration\":\"18446744073709551616\"", StringComparison.Ordinal));
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(overflowMetadata, payload)));
        byte[] nonNfcMetadata = Encoding.UTF8.GetBytes(
            Encoding.UTF8.GetString(metadata).Replace("session-v2", "session-e\u0301", StringComparison.Ordinal));
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(nonNfcMetadata, payload)));
        byte[] invalidUtf8Metadata = metadata.ToArray();
        int sessionOffset = Encoding.UTF8.GetString(metadata).IndexOf("session-v2", StringComparison.Ordinal);
        invalidUtf8Metadata[sessionOffset] = 0xff;
        ExpectReject(() => ParseAudioFrame(BuildAudioFrame(invalidUtf8Metadata, payload)));
        ExpectReject(() => RetryCommitment(retryKey, noncanonicalMetadata, payload));

        var maxU64Audio = audio with { Key = key with { CaptureGeneration = ulong.MaxValue } };
        ParseAudioFrame(EncodeAudioFrame(maxU64Audio));
        RunStateMatrix();
        RunLongDurationMatrix();

        Console.WriteLine("{\"phase\":\"2A-csharp-vectors\",\"successful\":true,\"vectorsRun\":48}");
    }

    private static void RunStateMatrix()
    {
        var firstEffect = new EffectFence("effect-1");
        var secondEffect = new EffectFence("effect-2");
        EffectToken token = firstEffect.Prepare("owner-a");
        if (firstEffect.Prepare("owner-a") != token)
            throw new InvalidOperationException("effect owner retry is not idempotent");
        secondEffect.Prepare("owner-a");
        ExpectReject(() => secondEffect.Invoke(token));
        ExpectReject(() => firstEffect.Prepare("owner-b"));
        firstEffect.Invoke(token);
        ExpectReject(() => firstEffect.Invoke(token));
        firstEffect.ProviderReturned(token);
        if (firstEffect.JournalCommitted)
            throw new InvalidOperationException("provider return advanced forwarding before journal");
        firstEffect.CommitJournal(token);
        ExpectReject(firstEffect.AcknowledgeProviderClose);
        ExpectReject(firstEffect.AcknowledgeOwnerTermination);
        firstEffect.Recover(1, 1);
        ExpectReject(firstEffect.Terminalize);
        firstEffect.AcknowledgeProviderClose();
        ExpectReject(firstEffect.Terminalize);
        firstEffect.AcknowledgeOwnerTermination();
        firstEffect.Terminalize();
        ExpectReject(() => firstEffect.Recover(2, 2));
        if (firstEffect.State != "terminal" || firstEffect.InvokeCount != 1 || !firstEffect.JournalCommitted)
            throw new InvalidOperationException("effect fencing state mismatch");

        var custody = new CustodyBudget(48_000, 2);
        ExpectReject(() => custody.Reserve(
            "oversized",
            new CustodyItem(96_000, 384_000, 472, 384_600, 0)));
        if (custody.Count != 0)
            throw new InvalidOperationException("oversized event mutated custody");
        var item = new CustodyItem(960, 3_840, 472, 4_440, 0);
        if (!custody.Reserve("discard", item) || custody.Reserve("discard", item))
            throw new InvalidOperationException("custody retry state mismatch");
        custody.Discard("discard", "gap-1");
        custody.Discard("discard", "gap-1");
        ExpectReject(() => custody.Discard("discard", "gap-2"));

        custody = new CustodyBudget(8_000, 1);
        custody.Reserve("expiring", new CustodyItem(160, 320, 472, 920, 0));
        if (custody.Advance(10_000, true).Count != 0 || !custody.AcquisitionStopped)
            throw new InvalidOperationException("custody reconcile deadline mismatch");
        if (!custody.Advance(30_000, true).SequenceEqual(new[] { "expiring" }))
            throw new InvalidOperationException("custody privacy deadline mismatch");

        custody = new CustodyBudget(8_000, 1);
        custody.Reserve("prepared-discard", new CustodyItem(160, 320, 472, 920, 0));
        var preparedDiscard = new EffectFence("effect-prepared-discard");
        EffectToken preparedDiscardToken = preparedDiscard.Prepare("owner-a");
        custody.RegisterEffect("prepared-discard", preparedDiscard);
        ExpectReject(() => custody.Discard("prepared-discard", "gap-deletion"));
        custody.CancelPreparedEffectAndDiscard("prepared-discard", preparedDiscard, "gap-deletion");
        custody.CancelPreparedEffectAndDiscard("prepared-discard", preparedDiscard, "gap-deletion");
        ExpectReject(() => preparedDiscard.Invoke(preparedDiscardToken));
        ExpectReject(() => preparedDiscard.Callback(preparedDiscardToken));
        ExpectReject(() => custody.InvokeEffect("prepared-discard", preparedDiscard, preparedDiscardToken));
        if (!preparedDiscard.CancelledWithoutInvoke || custody.GapFor("prepared-discard") != "gap-deletion")
            throw new InvalidOperationException("prepared effect cancellation/discard mismatch");

        custody = new CustodyBudget(8_000, 1);
        custody.Reserve("direct", new CustodyItem(160, 320, 472, 920, 0));
        var directEffect = new EffectFence("effect-direct");
        EffectToken directToken = directEffect.Prepare("owner-a");
        custody.RegisterEffect("direct", directEffect);
        custody.InvokeEffect("direct", directEffect, directToken);
        directEffect.ProviderReturned(directToken);
        directEffect.CommitJournal(directToken);
        ExpectReject(() => custody.Forward("direct", true));
        custody.ForwardEffect("direct", directEffect);
        if (!custody.IsForwarded("direct"))
            throw new InvalidOperationException("effect-bound direct forwarding was not recorded");

        custody = new CustodyBudget(8_000, 1);
        custody.Reserve("pending-forwarded", new CustodyItem(160, 320, 472, 920, 0));
        var pendingForwarded = new EffectFence("effect-pending-forwarded");
        EffectToken pendingToken = pendingForwarded.Prepare("owner-a");
        custody.RegisterEffect("pending-forwarded", pendingForwarded);
        custody.InvokeEffect("pending-forwarded", pendingForwarded, pendingToken);
        ExpectReject(() => custody.CancelPreparedEffectAndDiscard(
            "pending-forwarded", pendingForwarded, "gap-forbidden"));
        custody.LocalPrivacyRelease("pending-forwarded", "emergency_local");
        ExpectReject(() => custody.RegisterEffect("pending-forwarded", new EffectFence("replacement")));
        ExpectReject(() => custody.Discard("pending-forwarded", "gap-forbidden"));
        ExpectReject(() => custody.ResolvePendingEffect("pending-forwarded", pendingForwarded, "durable_discard"));
        var foreignPending = new EffectFence("effect-pending-forwarded");
        EffectToken foreignPendingToken = foreignPending.Prepare("owner-b");
        foreignPending.Invoke(foreignPendingToken);
        foreignPending.ProviderReturned(foreignPendingToken);
        foreignPending.CommitJournal(foreignPendingToken);
        ExpectReject(() => custody.ResolvePendingEffect("pending-forwarded", foreignPending, "forwarded"));
        pendingForwarded.ProviderReturned(pendingToken);
        pendingForwarded.CommitJournal(pendingToken);
        custody.ResolvePendingEffect("pending-forwarded", pendingForwarded, "forwarded");
        if (!custody.IsForwarded("pending-forwarded") || custody.GapFor("pending-forwarded") is not null)
            throw new InvalidOperationException("pending forwarded effect resolution mismatch");

        custody = new CustodyBudget(8_000, 1);
        custody.Reserve("pending-ambiguous", new CustodyItem(160, 320, 472, 920, 0));
        var pendingAmbiguous = new EffectFence("effect-pending-ambiguous");
        EffectToken ambiguousToken = pendingAmbiguous.Prepare("owner-a");
        custody.RegisterEffect("pending-ambiguous", pendingAmbiguous);
        custody.InvokeEffect("pending-ambiguous", pendingAmbiguous, ambiguousToken);
        custody.LocalPrivacyRelease("pending-ambiguous", "deletion_local");
        pendingAmbiguous.Recover(1, 1);
        pendingAmbiguous.AcknowledgeProviderClose();
        pendingAmbiguous.AcknowledgeOwnerTermination();
        pendingAmbiguous.Terminalize();
        custody.ResolvePendingEffect("pending-ambiguous", pendingAmbiguous, "ambiguous_effect");
        if (custody.GapFor("pending-ambiguous") != "ambiguous_effect" || custody.IsForwarded("pending-ambiguous"))
            throw new InvalidOperationException("pending ambiguous effect resolution mismatch");

        var deletion = new DeletionFence(
            new[] { "worker", "connection", "effect" },
            new[] { "session", "retry", "backup" });
        ulong generation = deletion.Request();
        deletion.Acknowledge("worker", generation);
        deletion.Acknowledge("connection", generation);
        ExpectReject(deletion.StartDeleting);
        deletion.Acknowledge("effect", generation);
        deletion.StartDeleting();
        var absent = new Dictionary<string, bool>(StringComparer.Ordinal)
        {
            ["session"] = true,
            ["retry"] = true,
            ["backup"] = true,
        };
        if (!deletion.RecordPass(1, absent))
            throw new InvalidOperationException("first absence pass failed");
        var failed = new Dictionary<string, bool>(absent, StringComparer.Ordinal) { ["backup"] = false };
        if (deletion.RecordPass(2, failed) || deletion.State != "deletion_failed")
            throw new InvalidOperationException("injected deletion failure was accepted");
        deletion.Resume(generation);
        if (!deletion.RecordPass(2, absent))
            throw new InvalidOperationException("resumed deletion pass failed");
        deletion.Finish();
        ExpectReject(() => deletion.RejectLateCallback(generation));
        if (deletion.State != "deleted" || deletion.LateCallbacks != 1)
            throw new InvalidOperationException("deletion terminal state mismatch");

        var edge = new TransportEdgeBudget();
        for (int index = 0; index < 16; index++)
            edge.Open($"connection-{index}", "192.0.2.1", 10_000, 16_384, 8_192, 32_768);
        long pendingBytes = edge.PendingBytes;
        ExpectReject(() => edge.Open("connection-16", "192.0.2.1", 10_000, 1, 1, 1));
        ExpectReject(() => edge.RejectPreAuthAudio(68_100));
        if (edge.PendingBytes != pendingBytes)
            throw new InvalidOperationException("pre-auth audio allocated receive custody");
        ExpectReject(() => edge.Authenticate("connection-0", 9_999));
        edge.Authenticate("connection-0", 18_000);
        ExpectReject(() => edge.Authenticate("connection-1", 18_001));
        if (edge.ParserBytes != 68_100)
            throw new InvalidOperationException("authenticated parser allocation mismatch");

        ExpectReject(() => _ = new QuotaBucket(-1, 1, 1, 1, 1, 1, 1));
        var overflowQuota = new QuotaBucket(1, 1, 1, 1, 1, 1, long.MaxValue);
        if (!overflowQuota.Reserve(0, 1, 1, 1, long.MaxValue) || overflowQuota.Reserve(0, 0, 0, 0, 1))
            throw new InvalidOperationException("quota custody overflow was accepted");
    }

    private static void RunLongDurationMatrix()
    {
        foreach (int minutes in new[] { 60, 90, 120 })
        {
            var sources = new[]
            {
                new QuotaBucket(50, 100, 192_000, 384_000, 205_000, 410_000, 1_048_576),
                new QuotaBucket(50, 100, 192_000, 384_000, 205_000, 410_000, 1_048_576),
            };
            var session = new QuotaBucket(100, 200, 384_000, 768_000, 410_000, 820_000, 2_097_152);
            var tenant = new QuotaBucket(400, 800, 1_536_000, 3_072_000, 1_640_000, 3_280_000, 8_388_608);
            var process = new QuotaBucket(1_600, 3_200, 6_144_000, 12_288_000, 6_560_000, 13_120_000, 33_554_432);
            int[] payloadBytes = { 320, 3_840 };
            int eventCount = checked(minutes * 60 * 50);
            for (int eventIndex = 0; eventIndex < eventCount; eventIndex++)
            {
                int second = eventIndex / 50;
                for (int sourceIndex = 0; sourceIndex < sources.Length; sourceIndex++)
                {
                    int bytes = payloadBytes[sourceIndex];
                    if (!sources[sourceIndex].Reserve(second, 1, bytes, 4_100, bytes) ||
                        !session.Reserve(second, 1, bytes, 4_100, bytes) ||
                        !tenant.Reserve(second, 1, bytes, 4_100, bytes) ||
                        !process.Reserve(second, 1, bytes, 4_100, bytes))
                    {
                        throw new InvalidOperationException("long-duration quota vector exceeded a frozen bound");
                    }
                    sources[sourceIndex].Release(bytes);
                    session.Release(bytes);
                    tenant.Release(bytes);
                    process.Release(bytes);
                }
            }
            if (sources.Any(source => source.Custody != 0) || session.Custody != 0 ||
                tenant.Custody != 0 || process.Custody != 0)
                throw new InvalidOperationException("long-duration custody grew across events");
        }
    }

    private static void ValidateAudio(AudioFrameInput input)
    {
        ValidateKey(input.Key);
        if (input.LastSampleExclusive <= input.FirstSample)
            throw new InvalidOperationException("audio sample range is empty");
        if (input.SampleRateHertz is < 8_000 or > 48_000 ||
            input.ChannelCount is < 1 or > 2 || input.DurationMs is < 20 or > 250)
        {
            throw new InvalidOperationException("audio format is outside v2 bounds");
        }
        ulong frames = input.LastSampleExclusive - input.FirstSample;
        int bytesPerFrame = checked(input.ChannelCount * 2);
        if (frames > (ulong)(int.MaxValue / bytesPerFrame) ||
            input.Payload.Length != checked((int)frames * bytesPerFrame) ||
            input.Payload.Length is < 1 or > 64_000 ||
            frames * 1_000 != (ulong)(input.DurationMs * input.SampleRateHertz) ||
            frames > Math.Min(96_000UL, (ulong)(2 * input.SampleRateHertz)))
        {
            throw new InvalidOperationException("audio payload does not match bounded format");
        }
    }

    private static string AudioEventId(AudioFrameInput input)
    {
        ValidateAudio(input);
        string identity = string.Join('\0',
            "tars-audio-event-v2", input.Key.SessionId, input.Key.StreamId,
            input.Key.CaptureGeneration.ToString(CultureInfo.InvariantCulture), input.Key.Source,
            input.Sequence.ToString(CultureInfo.InvariantCulture),
            input.FirstSample.ToString(CultureInfo.InvariantCulture),
            input.LastSampleExclusive.ToString(CultureInfo.InvariantCulture));
        return "aevt_" + Hex(Sha256(Encoding.UTF8.GetBytes(identity)));
    }

    private static byte[] CanonicalAudioMetadata(AudioFrameInput input)
    {
        ValidateAudio(input);
        string eventId = AudioEventId(input);
        string digest = Hex(Sha256(input.Payload));
        string text = "{\"captureGeneration\":\"" + input.Key.CaptureGeneration.ToString(CultureInfo.InvariantCulture) +
            "\",\"channelCount\":" + input.ChannelCount.ToString(CultureInfo.InvariantCulture) +
            ",\"durationMs\":" + input.DurationMs.ToString(CultureInfo.InvariantCulture) +
            ",\"encoding\":\"pcm_s16le\",\"eventId\":\"" + eventId +
            "\",\"eventType\":\"audio.chunk\",\"firstSample\":\"" + input.FirstSample.ToString(CultureInfo.InvariantCulture) +
            "\",\"lastSampleExclusive\":\"" + input.LastSampleExclusive.ToString(CultureInfo.InvariantCulture) +
            "\",\"payloadBytes\":" + input.Payload.Length.ToString(CultureInfo.InvariantCulture) +
            ",\"payloadDigestSha256\":\"" + digest +
            "\",\"protocolVersion\":2,\"sampleRateHertz\":" + input.SampleRateHertz.ToString(CultureInfo.InvariantCulture) +
            ",\"sequence\":\"" + input.Sequence.ToString(CultureInfo.InvariantCulture) +
            "\",\"sessionId\":\"" + input.Key.SessionId +
            "\",\"source\":\"" + input.Key.Source +
            "\",\"streamId\":\"" + input.Key.StreamId + "\"}";
        byte[] metadata = Encoding.UTF8.GetBytes(text);
        if (metadata.Length > 4_096)
            throw new InvalidOperationException("audio metadata exceeds 4096 bytes");
        return metadata;
    }

    private static byte[] EncodeAudioFrame(AudioFrameInput input) =>
        BuildAudioFrame(CanonicalAudioMetadata(input), input.Payload);

    private static byte[] BuildAudioFrame(byte[] metadata, byte[] payload)
    {
        if (metadata.Length > 4_096)
            throw new InvalidOperationException("audio metadata exceeds 4096 bytes");
        var frame = new List<byte>(checked(4 + metadata.Length + payload.Length));
        AppendU32(frame, checked((uint)metadata.Length));
        frame.AddRange(metadata);
        frame.AddRange(payload);
        if (frame.Count > 68_100)
            throw new InvalidOperationException("audio frame exceeds 68100 bytes");
        return frame.ToArray();
    }

    private static ParsedAudioFrame ParseAudioFrame(byte[] frame)
    {
        if (frame.Length is < 4 or > 68_100)
            throw new InvalidOperationException("audio frame size is invalid");
        uint metadataLength = BinaryPrimitives.ReadUInt32BigEndian(frame.AsSpan(0, 4));
        if (metadataLength is 0 or > 4_096 || 4L + metadataLength > frame.Length)
            throw new InvalidOperationException("declared audio metadata length is invalid");
        byte[] metadata = frame.AsSpan(4, checked((int)metadataLength)).ToArray();
        byte[] payload = frame.AsSpan(checked(4 + (int)metadataLength)).ToArray();
        try
        {
            using JsonDocument document = JsonDocument.Parse(metadata);
            JsonElement root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
                throw new InvalidOperationException("audio metadata must be an object");
            var values = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
            foreach (JsonProperty property in root.EnumerateObject())
            {
                if (!values.TryAdd(property.Name, property.Value))
                    throw new InvalidOperationException("duplicate audio metadata field");
            }
            string[] expectedFields =
            {
                "protocolVersion", "eventType", "sessionId", "streamId", "source",
                "captureGeneration", "eventId", "sequence", "firstSample",
                "lastSampleExclusive", "sampleRateHertz", "channelCount", "durationMs",
                "payloadBytes", "payloadDigestSha256", "encoding",
            };
            if (values.Count != expectedFields.Length || expectedFields.Any(field => !values.ContainsKey(field)))
                throw new InvalidOperationException("audio metadata fields are not exact");
            if (RequireInt(values, "protocolVersion", 2, 2) != 2 ||
                RequireString(values, "eventType") != "audio.chunk" ||
                RequireString(values, "encoding") != "pcm_s16le")
            {
                throw new InvalidOperationException("audio metadata type is invalid");
            }
            string sessionId = RequireString(values, "sessionId");
            string streamId = RequireString(values, "streamId");
            string source = RequireString(values, "source");
            string eventId = RequireString(values, "eventId");
            ValidateIdentifier("sessionId", sessionId);
            ValidateIdentifier("streamId", streamId);
            ValidateIdentifier("eventId", eventId);
            var key = new StreamKey(sessionId, streamId, RequireU64(values, "captureGeneration"), source);
            var input = new AudioFrameInput(
                key,
                RequireU64(values, "sequence"),
                RequireU64(values, "firstSample"),
                RequireU64(values, "lastSampleExclusive"),
                RequireInt(values, "sampleRateHertz", 8_000, 48_000),
                RequireInt(values, "channelCount", 1, 2),
                RequireInt(values, "durationMs", 20, 250),
                payload);
            int payloadBytes = RequireInt(values, "payloadBytes", 1, 64_000);
            string digest = RequireString(values, "payloadDigestSha256");
            if (digest.Length != 64 || digest.Any(value => value is not (>= '0' and <= '9' or >= 'a' and <= 'f')) ||
                payload.Length != payloadBytes || !CryptographicOperations.FixedTimeEquals(
                    Encoding.ASCII.GetBytes(digest), Encoding.ASCII.GetBytes(Hex(Sha256(payload)))))
            {
                throw new InvalidOperationException("audio payload length or digest mismatch");
            }
            ValidateAudio(input);
            string expectedEventId = AudioEventId(input);
            byte[] canonicalMetadata = CanonicalAudioMetadata(input);
            if (eventId != expectedEventId || !metadata.SequenceEqual(canonicalMetadata))
                throw new InvalidOperationException("metadata is not the canonical typed encoding");
            return new ParsedAudioFrame(input, eventId, metadata);
        }
        catch (JsonException error)
        {
            throw new InvalidOperationException("metadata is not valid JSON", error);
        }
    }

    private static string RequireString(IReadOnlyDictionary<string, JsonElement> values, string name)
    {
        JsonElement value = values[name];
        if (value.ValueKind != JsonValueKind.String)
            throw new InvalidOperationException($"{name} is not a string");
        return value.GetString() ?? throw new InvalidOperationException($"{name} is null");
    }

    private static int RequireInt(
        IReadOnlyDictionary<string, JsonElement> values,
        string name,
        int minimum,
        int maximum)
    {
        JsonElement value = values[name];
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetInt32(out int parsed) ||
            parsed < minimum || parsed > maximum)
        {
            throw new InvalidOperationException($"{name} is outside its checked integer domain");
        }
        return parsed;
    }

    private static ulong RequireU64(IReadOnlyDictionary<string, JsonElement> values, string name)
    {
        string text = RequireString(values, name);
        if (text.Length == 0 || (text.Length > 1 && text[0] == '0') ||
            text.Any(character => character is < '0' or > '9') ||
            !ulong.TryParse(text, NumberStyles.None, CultureInfo.InvariantCulture, out ulong parsed) ||
            parsed.ToString(CultureInfo.InvariantCulture) != text)
        {
            throw new InvalidOperationException($"{name} is not canonical uint64");
        }
        return parsed;
    }

    private static byte[] RetryCommitment(byte[] sessionKey, byte[] metadata, byte[] payload)
    {
        if (sessionKey.Length < 32 || metadata.Length > 4_096 || payload.Length > 64_000)
            throw new InvalidOperationException("retry commitment input is outside bounds");
        _ = ParseAudioFrame(BuildAudioFrame(metadata, payload));
        var message = new List<byte>(checked(14 + 4 + metadata.Length + 4 + payload.Length));
        message.AddRange(Encoding.UTF8.GetBytes("tars-retry-v2\0"));
        AppendU32(message, checked((uint)metadata.Length));
        message.AddRange(metadata);
        AppendU32(message, checked((uint)payload.Length));
        message.AddRange(payload);
        return HMACSHA256.HashData(sessionKey, message.ToArray());
    }

    private static void ExpectReject(Action action)
    {
        try
        {
            action();
        }
        catch (InvalidOperationException)
        {
            return;
        }

        throw new InvalidOperationException("invalid protocol-v2 vector was accepted");
    }

    private static void ValidateCanonicalString(string name, string value)
    {
        if (value.Contains('\0') || value != value.Normalize(NormalizationForm.FormC))
            throw new InvalidOperationException($"{name} must be NUL-free NFC");
    }

    private static void ValidateIdentifier(string name, string value)
    {
        ValidateCanonicalString(name, value);
        if (value.Length is < 1 or > 128 || !IsAsciiAlphanumeric(value[0]))
            throw new InvalidOperationException($"{name} is not a valid identifier");
        if (value.Skip(1).Any(character =>
                !IsAsciiAlphanumeric(character) && character is not ('.' or '_' or ':' or '-')))
        {
            throw new InvalidOperationException($"{name} is not a valid identifier");
        }
    }

    private static bool IsAsciiAlphanumeric(char value) =>
        value is >= '0' and <= '9' or >= 'A' and <= 'Z' or >= 'a' and <= 'z';

    private static void ValidateKey(StreamKey key)
    {
        ValidateIdentifier("sessionId", key.SessionId);
        ValidateIdentifier("streamId", key.StreamId);
        if (key.Source is not ("microphone" or "system_audio"))
            throw new InvalidOperationException("source is invalid");
    }

    private static byte[] IdentityPrefix(string prefix, StreamKey key)
    {
        ValidateCanonicalString("identity prefix", prefix);
        ValidateKey(key);
        return Encoding.UTF8.GetBytes(string.Join(
            '\0', prefix, key.SessionId, key.StreamId,
            key.CaptureGeneration.ToString(CultureInfo.InvariantCulture), key.Source));
    }

    private static byte[] Sha256(byte[] bytes) => SHA256.HashData(bytes);
    private static string Hex(byte[] bytes) => Convert.ToHexString(bytes).ToLowerInvariant();
    private static byte[] Concat(params byte[][] chunks) => chunks.SelectMany(chunk => chunk).ToArray();

    private static void AppendU32(List<byte> output, uint value)
    {
        Span<byte> bytes = stackalloc byte[4];
        BinaryPrimitives.WriteUInt32BigEndian(bytes, value);
        output.AddRange(bytes.ToArray());
    }

    private static void AppendU64(List<byte> output, ulong value)
    {
        Span<byte> bytes = stackalloc byte[8];
        BinaryPrimitives.WriteUInt64BigEndian(bytes, value);
        output.AddRange(bytes.ToArray());
    }

    private static void AppendString(List<byte> output, string value)
    {
        ValidateCanonicalString("identity string", value);
        byte[] bytes = Encoding.UTF8.GetBytes(value);
        AppendU32(output, checked((uint)bytes.Length));
        output.AddRange(bytes);
    }

    private static string AtomicId(Atomic item)
    {
        if (item.LastSampleExclusive <= item.FirstSample)
            throw new InvalidOperationException("atomic sample range is empty");
        string suffix = string.Concat(
            "\0", item.Sequence.ToString(CultureInfo.InvariantCulture),
            "\0", item.FirstSample.ToString(CultureInfo.InvariantCulture),
            "\0", item.LastSampleExclusive.ToString(CultureInfo.InvariantCulture));
        return "acov_" + Hex(Sha256(Concat(
            IdentityPrefix("tars-atomic-coverage-v2", item.Key),
            Encoding.UTF8.GetBytes(suffix))));
    }

    private static Atomic[] OrderedAtomic(StreamKey key, IReadOnlyList<Atomic> source)
    {
        ValidateKey(key);
        if (source.Count == 0 || source.Any(item => item.Key != key))
            throw new InvalidOperationException("atomic coverage list is invalid");

        var atomic = source
            .OrderBy(item => item.Sequence)
            .ThenBy(item => item.FirstSample)
            .ThenBy(item => item.LastSampleExclusive)
            .ThenBy(item => item.Id, StringComparer.Ordinal)
            .ToArray();
        if (atomic.Select(item => item.Id).Distinct(StringComparer.Ordinal).Count() != atomic.Length)
            throw new InvalidOperationException("duplicate atomic coverage identity");

        for (int leftIndex = 0; leftIndex < atomic.Length; leftIndex++)
        {
            for (int rightIndex = leftIndex + 1; rightIndex < atomic.Length; rightIndex++)
            {
                Atomic left = atomic[leftIndex];
                Atomic right = atomic[rightIndex];
                bool samplesDisjoint = left.LastSampleExclusive <= right.FirstSample ||
                                       right.LastSampleExclusive <= left.FirstSample;
                if (left.Sequence == right.Sequence || !samplesDisjoint)
                    throw new InvalidOperationException("overlapping atomic coverage");
            }
        }

        return atomic;
    }

    private static string TerminalId(StreamKey key, IReadOnlyList<Atomic> source)
    {
        Atomic[] atomic = OrderedAtomic(key, source);
        var bytes = new List<byte>(IdentityPrefix("tars-terminal-coverage-v2", key));
        AppendU32(bytes, checked((uint)atomic.Length));
        foreach (Atomic item in atomic) AppendString(bytes, item.Id);
        return "covr_" + Hex(Sha256(bytes.ToArray()));
    }

    private static string SegmentId(
        StreamKey key,
        IReadOnlyList<Atomic> source,
        ulong textFirstSample,
        ulong textLastSampleExclusive,
        ulong providerResultOrdinal,
        string providerName,
        string providerResultId,
        ulong? sttAttemptGeneration)
    {
        if (textLastSampleExclusive <= textFirstSample)
            throw new InvalidOperationException("segment sample range is empty");
        ValidateIdentifier("providerName", providerName);
        ValidateIdentifier("providerResultId", providerResultId);

        Atomic[] atomic = OrderedAtomic(key, source);
        var bytes = new List<byte>(IdentityPrefix("tars-transcript-segment-v2", key));
        AppendU32(bytes, checked((uint)atomic.Length));
        foreach (Atomic item in atomic) AppendString(bytes, item.Id);
        AppendU64(bytes, textFirstSample);
        AppendU64(bytes, textLastSampleExclusive);
        AppendU64(bytes, providerResultOrdinal);
        AppendString(bytes, providerName);
        AppendString(bytes, providerResultId);
        if (sttAttemptGeneration.HasValue)
        {
            bytes.Add(1);
            AppendU64(bytes, sttAttemptGeneration.Value);
        }
        else
        {
            bytes.Add(0);
        }
        return "seg_" + Hex(Sha256(bytes.ToArray()));
    }
}
