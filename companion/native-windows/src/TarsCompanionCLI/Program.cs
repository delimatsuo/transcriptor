using System;
using System.Net.WebSockets;
using System.Threading;
using System.Threading.Tasks;
using TarsNativeCompanion.Capture;
using TarsNativeCompanion.Contracts;
using TarsNativeCompanion.Protocol;

namespace TarsCompanionCLI;

public static class CaptureModeGate
{
    public static int Validate(bool simulate, bool isWindows)
    {
        return (simulate || isWindows) ? 0 : 2;
    }

    public static int Validate(bool simulate)
    {
        return Validate(simulate, OperatingSystem.IsWindows());
    }
}

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        string sessionId = "default";
        string gatewayBase = "ws://127.0.0.1:8000/api/stream/native";
        string token = "";
        bool simulate = false;

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--session-id" && i + 1 < args.Length)
            {
                sessionId = args[++i];
            }
            else if (args[i] == "--gateway" && i + 1 < args.Length)
            {
                gatewayBase = args[++i];
            }
            else if (args[i] == "--token" && i + 1 < args.Length)
            {
                token = args[++i];
            }
            else if (args[i] == "--simulate")
            {
                simulate = true;
            }
        }

        // Check if non-simulate mode is requested on non-Windows platforms
        int gateResult = CaptureModeGate.Validate(simulate);
        if (gateResult == 2)
        {
            Console.Error.WriteLine("ERRO: A captura WASAPI real requer Windows 10/11. Em outros sistemas operacionais, use --simulate.");
            return 2;
        }

        string urlString = $"{gatewayBase}/{sessionId}";

        if (!Uri.TryCreate(urlString, UriKind.Absolute, out var uri))
        {
            Console.Error.WriteLine($"[tars-companion] Invalid gateway URL: {urlString}");
            return 1;
        }

        Console.WriteLine("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
        Console.WriteLine("  T.A.R.S. Native Windows Companion (WASAPI Architecture)  ");
        Console.WriteLine("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
        Console.WriteLine($"Session ID:   {sessionId}");
        Console.WriteLine($"Gateway URL:  {urlString}");
        Console.WriteLine($"Capture Mode: {(simulate ? "Simulated WASAPI Audio Engine" : "Native Windows WASAPI (Direct Render + Microphone)")}");
        Console.WriteLine("Driver Setup: ZERO virtual devices or VB-CABLE required");
        Console.WriteLine("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

        using var cts = new CancellationTokenSource();
        Console.CancelKeyPress += (s, e) =>
        {
            e.Cancel = true;
            cts.Cancel();
        };

        using var clientWebSocket = new ClientWebSocket();
        if (!string.IsNullOrEmpty(token))
        {
            clientWebSocket.Options.AddSubProtocol("tars-stream");
            clientWebSocket.Options.AddSubProtocol(token);
        }
        try
        {
            await clientWebSocket.ConnectAsync(uri, cts.Token);
            Console.WriteLine("✓ Connected to T.A.R.S. streaming gateway");
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"⚠ Gateway connection failed: {ex.Message}");
            return 1;
        }

        await using var sink = new WebSocketAudioSink(clientWebSocket, sessionId);

        var micIdentity = new SourceIdentity(
            sessionId: sessionId,
            streamId: "mic",
            captureGeneration: 1,
            source: AudioSource.Microphone,
            sampleRate: 16000,
            channelCount: 1
        );

        var sysIdentity = new SourceIdentity(
            sessionId: sessionId,
            streamId: "system",
            captureGeneration: 1,
            source: AudioSource.SystemAudio,
            sampleRate: 16000,
            channelCount: 1
        );

        var micConfig = new CaptureSourceConfiguration(micIdentity, "Wasapi.DefaultCommunicationsMic");
        var sysConfig = new CaptureSourceConfiguration(sysIdentity, "Wasapi.DefaultRenderLoopback");

        await using var micSource = new WasapiMicrophoneAudioSource(micConfig, liveCaptureEnabled: true, sink: sink, simulated: simulate);
        await using var sysSource = new WasapiLoopbackSystemAudioSource(sysConfig, liveCaptureEnabled: true, sink: sink, simulated: simulate);

        try
        {
            await micSource.StartAsync();
            Console.WriteLine("✓ Microphone capture active (WASAPI)");
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"⚠ Microphone capture failed: {ex.Message}");
        }

        try
        {
            await sysSource.StartAsync();
            Console.WriteLine("✓ System audio capture active (WASAPI Loopback)");
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"⚠ System audio capture failed: {ex.Message}");
        }

        Console.WriteLine("\nStreaming dual-channel audio to T.A.R.S. gateway...");
        Console.WriteLine("Press Ctrl+C to terminate.\n");

        // Background loop to detect server-side WebSocket closure
        _ = Task.Run(async () =>
        {
            var buffer = new byte[512];
            try
            {
                while (clientWebSocket.State == WebSocketState.Open && !cts.IsCancellationRequested)
                {
                    var res = await clientWebSocket.ReceiveAsync(new ArraySegment<byte>(buffer), cts.Token);
                    if (res.MessageType == WebSocketMessageType.Close)
                    {
                        Console.WriteLine("\n[tars-companion] Conexão encerrada pelo servidor (sessão finalizada).");
                        cts.Cancel();
                        break;
                    }
                }
            }
            catch (OperationCanceledException) { }
            catch (Exception)
            {
                if (!cts.IsCancellationRequested)
                {
                    Console.WriteLine("\n[tars-companion] Conexão com o gateway perdida.");
                    cts.Cancel();
                }
            }
        });

        try
        {
            await Task.Delay(Timeout.Infinite, cts.Token);
        }
        catch (OperationCanceledException)
        {
            Console.WriteLine("\nTerminating capture cleanly...");
        }

        await micSource.StopAsync();
        await sysSource.StopAsync();
        Console.WriteLine("✓ Graceful teardown complete");
        return 0;
    }
}
