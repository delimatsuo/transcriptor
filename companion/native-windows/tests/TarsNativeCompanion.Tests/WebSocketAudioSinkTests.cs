using System;
using System.Buffers.Binary;
using System.Text;
using System.Text.Json;
using TarsNativeCompanion.Contracts;
using TarsNativeCompanion.Protocol;
using Xunit;

namespace TarsNativeCompanion.Tests;

public class WebSocketAudioSinkTests
{
    [Fact]
    public void TestFramePacketBinaryFramingAndHeaderJson()
    {
        var identity = new SourceIdentity("pilot-session-123", "mic-1", 1, AudioSource.Microphone, 16000, 1);
        byte[] pcmPayload = new byte[1600]; // 50ms @ 16kHz mono
        for (int i = 0; i < pcmPayload.Length; i++)
        {
            pcmPayload[i] = (byte)(i % 256);
        }

        var frame = new AudioFrame(identity, sequence: 42, firstSample: 32800, capturedAtMs: 2050, null, pcmPayload);

        byte[] packet = WebSocketAudioSink.SerializeFramePacket(frame, "pilot-session-123");

        // Verify minimum length
        Assert.True(packet.Length > 4 + 1600);

        // Verify 4-byte big-endian header length
        uint headerLen = BinaryPrimitives.ReadUInt32BigEndian(packet.AsSpan(0, 4));
        Assert.True(headerLen > 0);
        Assert.Equal((uint)(packet.Length - 4 - 1600), headerLen);

        // Verify JSON header contents
        string headerJson = Encoding.UTF8.GetString(packet, 4, (int)headerLen);
        using var doc = JsonDocument.Parse(headerJson);
        var root = doc.RootElement;

        Assert.Equal("pilot-session-123", root.GetProperty("session_id").GetString());
        Assert.Equal("microphone", root.GetProperty("source").GetString());
        Assert.Equal(42UL, root.GetProperty("sequence").GetUInt64());
        Assert.Equal(32800UL, root.GetProperty("first_sample").GetUInt64());
        Assert.Equal(2050UL, root.GetProperty("captured_at_ms").GetUInt64());
        Assert.Equal(16000, root.GetProperty("sample_rate").GetInt32());
        Assert.Equal(1, root.GetProperty("channel_count").GetInt32());
        Assert.Equal(50UL, root.GetProperty("duration_ms").GetUInt64());

        // Verify payload bytes
        byte[] payloadExtracted = packet.AsSpan(4 + (int)headerLen).ToArray();
        Assert.Equal(pcmPayload, payloadExtracted);
    }

    [Fact]
    public void TestGapMessageSerializationMatchesWireFormat()
    {
        var identity = new SourceIdentity("session-gap", "sys-1", 1, AudioSource.SystemAudio, 16000, 1);
        var gap = new CoverageGap(
            identity,
            firstSample: 800,
            lastSampleExclusive: 1600,
            reason: GapReason.Overflow,
            firstSequence: 2,
            lastSequenceExclusive: 3,
            firstCapturedAtMs: 1050,
            lastCapturedAtMs: 1100,
            deviceId: "wasapi-render",
            firstCapturedAtMonotonicNs: 1050000000UL,
            lastCapturedAtMonotonicNs: 1100000000UL,
            firstCapturedAtWallClockMs: 1050UL,
            lastCapturedAtWallClockMs: 1100UL,
            boundary: GapBoundary.KnownRange
        );

        string gapJson = WebSocketAudioSink.SerializeGapMessage(gap);
        using var doc = JsonDocument.Parse(gapJson);
        var root = doc.RootElement;

        Assert.Equal("gap", root.GetProperty("type").GetString());
        Assert.Equal("system_audio", root.GetProperty("source").GetString());
        Assert.Equal("overflow", root.GetProperty("reason").GetString());
        Assert.Equal(800UL, root.GetProperty("first_sample").GetUInt64());
    }
}
