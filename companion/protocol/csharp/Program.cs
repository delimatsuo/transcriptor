using System.Buffers.Binary;
using System.Security.Cryptography;
using System.Text;

// Pure, dependency-free v2 vector runner. It intentionally contains no
// networking, device, provider, filesystem, or credential integration.
readonly record struct StreamKey(string SessionId, string StreamId, ulong CaptureGeneration, string Source);
readonly record struct Atomic(StreamKey Key, ulong Sequence, ulong FirstSample, ulong LastSampleExclusive)
{
    public string Id => "acov_" + Hex(Sha256(IdentityPrefix("tars-atomic-coverage-v2", Key) +
        Encoding.UTF8.GetBytes($"\0{Sequence}\0{FirstSample}\0{LastSampleExclusive}")));
}

static byte[] IdentityPrefix(string prefix, StreamKey key) => Encoding.UTF8.GetBytes(
    string.Join('\0', prefix, key.SessionId, key.StreamId, key.CaptureGeneration.ToString(), key.Source));

static byte[] Sha256(byte[] bytes) => SHA256.HashData(bytes);
static string Hex(byte[] bytes) => Convert.ToHexString(bytes).ToLowerInvariant();

static void AppendU32(List<byte> output, uint value)
{
    Span<byte> bytes = stackalloc byte[4];
    BinaryPrimitives.WriteUInt32BigEndian(bytes, value);
    output.AddRange(bytes.ToArray());
}

static void AppendU64(List<byte> output, ulong value)
{
    Span<byte> bytes = stackalloc byte[8];
    BinaryPrimitives.WriteUInt64BigEndian(bytes, value);
    output.AddRange(bytes.ToArray());
}

static void AppendString(List<byte> output, string value)
{
    if (value.Contains('\0') || value != value.Normalize(NormalizationForm.FormC))
        throw new InvalidOperationException("non-canonical string");
    byte[] bytes = Encoding.UTF8.GetBytes(value);
    AppendU32(output, checked((uint)bytes.Length));
    output.AddRange(bytes);
}

static string TerminalId(StreamKey key, IReadOnlyList<Atomic> source)
{
    var atomic = source.OrderBy(item => item.Sequence).ThenBy(item => item.FirstSample).ThenBy(item => item.LastSampleExclusive).ThenBy(item => item.Id).ToArray();
    var bytes = new List<byte>(IdentityPrefix("tars-terminal-coverage-v2", key));
    AppendU32(bytes, checked((uint)atomic.Length));
    foreach (var item in atomic) AppendString(bytes, item.Id);
    return "covr_" + Hex(Sha256(bytes.ToArray()));
}

static string SegmentId(StreamKey key, IReadOnlyList<Atomic> source)
{
    var bytes = new List<byte>(IdentityPrefix("tars-transcript-segment-v2", key));
    var atomic = source.OrderBy(item => item.Sequence).ThenBy(item => item.FirstSample).ThenBy(item => item.LastSampleExclusive).ThenBy(item => item.Id).ToArray();
    AppendU32(bytes, checked((uint)atomic.Length));
    foreach (var item in atomic) AppendString(bytes, item.Id);
    AppendU64(bytes, 20);
    AppendU64(bytes, 120);
    AppendU64(bytes, 0);
    AppendString(bytes, "fixture");
    AppendString(bytes, "result-1");
    bytes.Add(1);
    AppendU64(bytes, 2);
    return "seg_" + Hex(Sha256(bytes.ToArray()));
}

var key = new StreamKey("session-v2", "stream-mic", 4, "microphone");
var first = new Atomic(key, 0, 0, 160);
var second = new Atomic(key, 1, 160, 320);
var expectedFirst = "acov_9646759fd911e57a6aa8eceb7101c1b86107b24b53fa0db200beb351b8ed6923";
var expectedSecond = "acov_7253b33653a2042851ea98d4b59a302b62594fba658f19925fd16c6646c90895";
var expectedTerminal = "covr_b501309bf531e3b7dc293857fc50752387fa7de3b48650820fff33d4024bb939";
var expectedSegment = "seg_5bff65fae2a94b2ed887183957588ed3d650bcf1c87696f9d089f5a95282a50f";
if (first.Id != expectedFirst || second.Id != expectedSecond || TerminalId(key, new[] { second, first }) != expectedTerminal || SegmentId(key, new[] { first }) != expectedSegment)
    throw new InvalidOperationException("protocol-v2 vector mismatch");
Console.WriteLine("{\"phase\":\"2A-csharp-vectors\",\"successful\":true,\"vectorsRun\":4}");
