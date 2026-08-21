using System;
using System.Buffers.Binary;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using TarsNativeCompanion.Capture;
using TarsNativeCompanion.Contracts;

namespace TarsNativeCompanion.Protocol;

public class WebSocketAudioSink : ICaptureFrameSink, IAsyncDisposable
{
    private readonly ClientWebSocket _webSocket;
    private readonly string _sessionId;
    private readonly SemaphoreSlim _sendLock = new(1, 1);
    private bool _disposed;

    public WebSocketAudioSink(ClientWebSocket webSocket, string sessionId)
    {
        _webSocket = webSocket;
        _sessionId = sessionId;
    }

    public static byte[] SerializeFramePacket(AudioFrame frame, string sessionId)
    {
        var headerObj = new
        {
            session_id = sessionId,
            source = frame.Identity.Source.ToWireString(),
            sequence = frame.Sequence,
            first_sample = frame.FirstSample,
            captured_at_ms = frame.CapturedAtMs,
            sample_rate = frame.Identity.SampleRate,
            channel_count = frame.Identity.ChannelCount,
            duration_ms = frame.DurationMs
        };

        byte[] headerBytes = JsonSerializer.SerializeToUtf8Bytes(headerObj);
        byte[] payloadBytes = frame.Payload.CopyData();

        byte[] packet = new byte[4 + headerBytes.Length + payloadBytes.Length];
        BinaryPrimitives.WriteUInt32BigEndian(packet.AsSpan(0, 4), (uint)headerBytes.Length);
        Buffer.BlockCopy(headerBytes, 0, packet, 4, headerBytes.Length);
        Buffer.BlockCopy(payloadBytes, 0, packet, 4 + headerBytes.Length, payloadBytes.Length);

        return packet;
    }

    public static string SerializeGapMessage(CoverageGap gap)
    {
        var gapObj = new
        {
            type = "gap",
            source = gap.Identity.Source.ToWireString(),
            reason = gap.Reason.ToWireString(),
            first_sample = gap.FirstSample ?? 0
        };

        return JsonSerializer.Serialize(gapObj);
    }

    public async ValueTask ReceiveFrameAsync(AudioFrame frame)
    {
        if (_webSocket.State != WebSocketState.Open || _disposed) return;

        byte[] packet = SerializeFramePacket(frame, _sessionId);

        await _sendLock.WaitAsync().ConfigureAwait(false);
        try
        {
            if (_webSocket.State == WebSocketState.Open)
            {
                await _webSocket.SendAsync(
                    new ArraySegment<byte>(packet),
                    WebSocketMessageType.Binary,
                    endOfMessage: true,
                    CancellationToken.None
                ).ConfigureAwait(false);
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[tars-companion] WebSocket audio frame send error: {ex.Message}");
        }
        finally
        {
            _sendLock.Release();
        }
    }

    public async ValueTask ReceiveGapAsync(CoverageGap gap)
    {
        if (_webSocket.State != WebSocketState.Open || _disposed) return;

        string gapJson = SerializeGapMessage(gap);
        byte[] textBytes = Encoding.UTF8.GetBytes(gapJson);

        await _sendLock.WaitAsync().ConfigureAwait(false);
        try
        {
            if (_webSocket.State == WebSocketState.Open)
            {
                await _webSocket.SendAsync(
                    new ArraySegment<byte>(textBytes),
                    WebSocketMessageType.Text,
                    endOfMessage: true,
                    CancellationToken.None
                ).ConfigureAwait(false);
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"[tars-companion] WebSocket gap send error: {ex.Message}");
        }
        finally
        {
            _sendLock.Release();
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (_disposed) return;
        _disposed = true;

        if (_webSocket.State == WebSocketState.Open)
        {
            try
            {
                await _webSocket.CloseAsync(
                    WebSocketCloseStatus.NormalClosure,
                    "Client closing",
                    CancellationToken.None
                ).ConfigureAwait(false);
            }
            catch
            {
                // Ignore errors during disposal
            }
        }
        _sendLock.Dispose();
        _webSocket.Dispose();
    }
}
