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

        Console.WriteLine("{\"phase\":\"2A-csharp-vectors\",\"successful\":true,\"vectorsRun\":21}");
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
