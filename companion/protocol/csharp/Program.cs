using System.Buffers.Binary;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;

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

        Console.WriteLine("{\"phase\":\"2A-csharp-vectors\",\"successful\":true,\"vectorsRun\":11}");
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
