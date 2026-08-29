"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  encodeAudioFrame,
  float32ToInt16,
  resampleTo16k,
  type FrameMetadata,
} from "@/lib/browserPcmEncoder";
import { buildStreamSocketConfig } from "@/lib/streamUrl";
import { apiFetch } from "@/lib/auth";
import { getRuntimeConfig } from "@/lib/runtimeConfig";
import {
  IAP_AUTH_TERMINAL_EVENT,
  emitIapTerminalAuthEvent,
} from "@/lib/iapSession";
import {
  boundedRetryDelay,
  cancelLifecycleRetry,
  commitIfCurrent,
  commitAsyncResource,
  isIapTerminalClose,
  isIapTerminalHttpStatus,
  lifecycleAttemptIsCurrent,
  scheduleLifecycleRetry,
} from "@/lib/iapLifecycle";

const runtimeConfig = getRuntimeConfig();
const WS_STREAM_BASE = runtimeConfig.streamWsUrl;
const API_BASE_URL = runtimeConfig.apiOrigin;
const STORAGE_KEY_MIC = "tars_selected_mic_device_id";

export interface AudioInputDevice {
  deviceId: string;
  label: string;
  groupId: string;
}

export type PermissionState = "prompt" | "granted" | "denied" | "unsupported";

export interface UseBrowserAudioCaptureReturn {
  devices: AudioInputDevice[];
  selectedDeviceId: string;
  selectDevice: (deviceId: string) => Promise<void>;
  audioLevel: number; // 0.0 to 1.0
  isStreaming: boolean;
  permissionState: PermissionState;
  requestPermission: () => Promise<boolean>;
  startStreaming: (sessionId: string, streamKey?: string) => Promise<void>;
  stopStreaming: () => void;
  lastError: string | null;
}

export function useBrowserAudioCapture(): UseBrowserAudioCaptureReturn {
  const [devices, setDevices] = useState<AudioInputDevice[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>("");
  const [audioLevel, setAudioLevel] = useState<number>(0);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [permissionState, setPermissionState] =
    useState<PermissionState>("prompt");
  const [lastError, setLastError] = useState<string | null>(null);

  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const sourceNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const processorNodeRef = useRef<ScriptProcessorNode | null>(null);
  const analyserNodeRef = useRef<AnalyserNode | null>(null);
  const silentGainNodeRef = useRef<GainNode | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const streamReconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const streamReconnectDelayRef = useRef(1000);
  const streamAttemptGenerationRef = useRef(0);
  const audioGraphGenerationRef = useRef(0);
  const startStreamingRef = useRef<((sessionId: string, streamKey?: string) => Promise<void>) | null>(null);
  const helloReadySocketRef = useRef<WebSocket | null>(null);

  const activeSessionIdRef = useRef<string | null>(null);
  const sequenceRef = useRef<number>(0);
  const sampleOffsetRef = useRef<number>(0);
  const pcmBufferRef = useRef<Float32Array>(new Float32Array(0));

  // Enumerate input devices
  const updateDeviceList = useCallback(async () => {
    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices?.enumerateDevices
    ) {
      setPermissionState("unsupported");
      return;
    }
    try {
      const allDevices = await navigator.mediaDevices.enumerateDevices();
      const audioInputs = allDevices
        .filter((d) => d.kind === "audioinput")
        .map((d, index) => ({
          deviceId: d.deviceId,
          label:
            d.label ||
            `Microfone ${index + 1} (${d.deviceId.slice(0, 5)}...)`,
          groupId: d.groupId,
        }));

      setDevices(audioInputs);

      // Check if we have labels (indicates permission granted)
      const hasLabels = audioInputs.some((d) => d.label && !d.label.startsWith("Microfone "));
      if (hasLabels) {
        setPermissionState("granted");
      }

      // Maintain or set initial selected device
      const savedId =
        typeof window !== "undefined"
          ? localStorage.getItem(STORAGE_KEY_MIC)
          : null;
      if (savedId && audioInputs.some((d) => d.deviceId === savedId)) {
        setSelectedDeviceId(savedId);
      } else if (audioInputs.length > 0 && !selectedDeviceId) {
        const defaultDev =
          audioInputs.find((d) => d.deviceId === "default") || audioInputs[0];
        setSelectedDeviceId(defaultDev.deviceId);
      }
    } catch (err) {
      setLastError(
        err instanceof Error ? err.message : "Falha ao listar microfones.",
      );
    }
  }, [selectedDeviceId]);

  // Request microphone permission & refresh device list
  const requestPermission = useCallback(async (): Promise<boolean> => {
    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices?.getUserMedia
    ) {
      setPermissionState("unsupported");
      return false;
    }
    try {
      const tempStream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });
      // Release temporary track immediately
      tempStream.getTracks().forEach((t) => t.stop());
      setPermissionState("granted");
      await updateDeviceList();
      return true;
    } catch {
      setPermissionState("denied");
      setLastError("Permissão de microfone negada pelo navegador.");
      return false;
    }
  }, [updateDeviceList]);

  // Handle live audio level analysis via requestAnimationFrame
  const startLevelMeter = useCallback((analyser: AnalyserNode) => {
    const dataArray = new Uint8Array(analyser.frequencyBinCount);

    const updateMeter = () => {
      analyser.getByteFrequencyData(dataArray);
      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) {
        sum += dataArray[i];
      }
      const avg = sum / dataArray.length;
      // Normalize to 0.0 - 1.0 with a perceptual curve
      const normalized = Math.min(1.0, Math.pow(avg / 128, 1.5));
      setAudioLevel(normalized);
      animFrameRef.current = requestAnimationFrame(updateMeter);
    };

    if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    animFrameRef.current = requestAnimationFrame(updateMeter);
  }, []);

  // Stop level meter animation
  const stopLevelMeter = useCallback(() => {
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    setAudioLevel(0);
  }, []);

  // Setup Web Audio graph for the given device ID
  const setupAudioGraph = useCallback(
    async (
      deviceId: string,
      expectedStreamGeneration = streamAttemptGenerationRef.current,
      expectedGraphGeneration = audioGraphGenerationRef.current,
    ) => {
      const graphGeneration = expectedGraphGeneration;
      const graphIsCurrent = () =>
        lifecycleAttemptIsCurrent(
          graphGeneration,
          audioGraphGenerationRef.current,
          "graph",
          "graph",
        ) &&
        lifecycleAttemptIsCurrent(
          expectedStreamGeneration,
          streamAttemptGenerationRef.current,
          "stream",
          "stream",
        );
      let stream: MediaStream | null = null;
      let ctx: AudioContext | null = null;
      let ownsContext = false;
      let sourceNode: MediaStreamAudioSourceNode | null = null;
      let analyser: AnalyserNode | null = null;
      let processor: ScriptProcessorNode | null = null;
      let silentGain: GainNode | null = null;

      const disconnectNode = (node: AudioNode | null) => {
        try {
          node?.disconnect();
        } catch {
          // Teardown is idempotent across browser implementations.
        }
      };
      const disposeNewGraph = async () => {
        disconnectNode(processor);
        disconnectNode(sourceNode);
        disconnectNode(analyser);
        disconnectNode(silentGain);
        stream?.getTracks().forEach((track) => track.stop());
        if (ownsContext && ctx) {
          try {
            await ctx.close();
          } catch {
            // Closing a context that failed during setup is best effort.
          }
        }
      };

      const constraints: MediaStreamConstraints = {
        audio: deviceId
          ? {
              deviceId: { exact: deviceId },
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: true,
            }
          : {
              echoCancellation: true,
              noiseSuppression: true,
              autoGainControl: true,
            },
      };

      try {
        const acquiredStream = await navigator.mediaDevices.getUserMedia(constraints);
        stream = await commitAsyncResource(
          acquiredStream,
          graphIsCurrent,
          async (staleStream) => {
            staleStream.getTracks().forEach((track) => track.stop());
          },
        );
        if (!stream) return;

        const AudioContextClass =
          window.AudioContext ||
          (window as unknown as { webkitAudioContext: typeof AudioContext })
            .webkitAudioContext;
        const existingContext = audioContextRef.current;
        ctx = existingContext && existingContext.state !== "closed"
          ? existingContext
          : new AudioContextClass();
        ownsContext = ctx !== existingContext;
        if (ctx.state === "suspended") {
          await ctx.resume();
        }
        if (!graphIsCurrent()) {
          await disposeNewGraph();
          return;
        }

        sourceNode = ctx.createMediaStreamSource(stream);
        analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        analyser.smoothingTimeConstant = 0.5;
        sourceNode.connect(analyser);

        // ScriptProcessor downsamples and emits 50ms chunks (800 samples at 16kHz)
        const bufferSize = 2048;
        processor = ctx.createScriptProcessor(bufferSize, 1, 1);

        processor.onaudioprocess = (e: AudioProcessingEvent) => {
        if (!graphIsCurrent()) return;
        const inputData = e.inputBuffer.getChannelData(0);
        const currentWs = wsRef.current;
        if (
          !activeSessionIdRef.current ||
          !currentWs ||
          currentWs.readyState !== WebSocket.OPEN ||
          helloReadySocketRef.current !== currentWs
        ) {
          return;
        }

        // Resample input to 16,000 Hz
        const resampled = resampleTo16k(inputData, ctx!.sampleRate, 16000);

        // Append to accumulation buffer
        const prevBuf = pcmBufferRef.current;
        const newBuf = new Float32Array(prevBuf.length + resampled.length);
        newBuf.set(prevBuf, 0);
        newBuf.set(resampled, prevBuf.length);
        pcmBufferRef.current = newBuf;

        // Process 50ms chunks (50ms * 16 samples/ms = 800 samples)
        const CHUNK_SAMPLES = 800;
        while (pcmBufferRef.current.length >= CHUNK_SAMPLES) {
          const chunkFloat = pcmBufferRef.current.subarray(0, CHUNK_SAMPLES);
          pcmBufferRef.current = pcmBufferRef.current.subarray(CHUNK_SAMPLES);

          const chunkInt16 = float32ToInt16(chunkFloat);
          const seq = sequenceRef.current++;
          const firstSample = sampleOffsetRef.current;
          sampleOffsetRef.current += CHUNK_SAMPLES;

          const meta: FrameMetadata = {
            session_id: activeSessionIdRef.current,
            source: "microphone",
            sequence: seq,
            first_sample: firstSample,
            captured_at_ms: Date.now(),
            duration_ms: 50,
            sample_rate: 16000,
            channel_count: 1,
          };

          const binaryPacket = encodeAudioFrame(meta, chunkInt16);
          try {
            currentWs.send(binaryPacket);
          } catch {
            // Socket write failed, ignore transient error
          }
        }
        };

        sourceNode.connect(processor);
        // Connect to destination via silent gain to keep processor running
        silentGain = ctx.createGain();
        silentGain.gain.value = 0;
        processor.connect(silentGain);
        silentGain.connect(ctx.destination);

        if (!graphIsCurrent()) {
          await disposeNewGraph();
          return;
        }

        startLevelMeter(analyser!);
        if (!graphIsCurrent()) {
          await disposeNewGraph();
          return;
        }
        // Commit only after getUserMedia, context setup, and every node have
        // succeeded. The old graph is torn down at this single commit point.
        disconnectNode(processorNodeRef.current);
        disconnectNode(sourceNodeRef.current);
        disconnectNode(analyserNodeRef.current);
        disconnectNode(silentGainNodeRef.current);
        mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
        mediaStreamRef.current = stream;
        audioContextRef.current = ctx;
        sourceNodeRef.current = sourceNode;
        analyserNodeRef.current = analyser;
        processorNodeRef.current = processor;
        silentGainNodeRef.current = silentGain;
      } catch (error) {
        await disposeNewGraph();
        throw error;
      }
    },
    [startLevelMeter],
  );

  // Switch microphone device on the fly
  const selectDevice = useCallback(
    async (deviceId: string) => {
      setSelectedDeviceId(deviceId);
      const graphGeneration = audioGraphGenerationRef.current + 1;
      audioGraphGenerationRef.current = graphGeneration;
      if (typeof window !== "undefined") {
        localStorage.setItem(STORAGE_KEY_MIC, deviceId);
      }
      if (isStreaming || mediaStreamRef.current) {
        try {
          await setupAudioGraph(
            deviceId,
            streamAttemptGenerationRef.current,
            graphGeneration,
          );
        } catch (err) {
          if (graphGeneration === audioGraphGenerationRef.current) {
            setLastError(
              err instanceof Error ? err.message : "Falha ao trocar de microfone.",
            );
          }
        }
      }
    },
    [isStreaming, setupAudioGraph],
  );

  // Stop streaming & release resources
  const stopStreaming = useCallback(() => {
    streamAttemptGenerationRef.current += 1;
    audioGraphGenerationRef.current += 1;
    helloReadySocketRef.current = null;
    activeSessionIdRef.current = null;
    cancelLifecycleRetry(streamReconnectTimerRef.current);
    streamReconnectTimerRef.current = null;
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (processorNodeRef.current) {
      processorNodeRef.current.disconnect();
      processorNodeRef.current = null;
    }
    if (sourceNodeRef.current) {
      sourceNodeRef.current.disconnect();
      sourceNodeRef.current = null;
    }
    if (analyserNodeRef.current) {
      analyserNodeRef.current.disconnect();
      analyserNodeRef.current = null;
    }
    if (silentGainNodeRef.current) {
      silentGainNodeRef.current.disconnect();
      silentGainNodeRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }
    if (audioContextRef.current) {
      void audioContextRef.current.close();
      audioContextRef.current = null;
    }
    stopLevelMeter();
    setIsStreaming(false);
  }, [stopLevelMeter]);

  // Start live streaming to native stream gateway
  const startStreaming = useCallback(
    async (sessionId: string, streamKey?: string) => {
      if (!runtimeConfig.iap && !streamKey) {
        setLastError("Chave do fluxo de áudio ausente.");
        return;
      }

      const attemptGeneration = streamAttemptGenerationRef.current + 1;
      streamAttemptGenerationRef.current = attemptGeneration;
      const graphAttemptGeneration = audioGraphGenerationRef.current + 1;
      audioGraphGenerationRef.current = graphAttemptGeneration;
      const iapAttemptIsCurrent = () =>
        lifecycleAttemptIsCurrent(
          attemptGeneration,
          streamAttemptGenerationRef.current,
          sessionId,
          activeSessionIdRef.current,
        );
      activeSessionIdRef.current = sessionId;

      const scheduleRetry = () => {
        if (!iapAttemptIsCurrent() || !activeSessionIdRef.current) return;
        if (streamReconnectTimerRef.current) return;
        const delay = streamReconnectDelayRef.current;
        streamReconnectTimerRef.current = scheduleLifecycleRetry(iapAttemptIsCurrent, delay, () => {
          streamReconnectTimerRef.current = null;
          if (activeSessionIdRef.current === sessionId) {
            void startStreamingRef.current?.(sessionId, streamKey);
          }
        });
        streamReconnectDelayRef.current = boundedRetryDelay(delay, 30000);
      };

      let streamCredential = streamKey;
      if (runtimeConfig.iap) {
        try {
          const ticketResponse = await apiFetch(
            `${API_BASE_URL}/api/sessions/${encodeURIComponent(sessionId)}/stream-ticket`,
            { method: "POST" },
          );
          if (!iapAttemptIsCurrent()) return;
          if (!ticketResponse.ok) {
            if (runtimeConfig.iap && isIapTerminalHttpStatus(ticketResponse.status)) {
              stopStreaming();
              emitIapTerminalAuthEvent();
              return;
            }
            throw new Error("A sessão autenticada do áudio expirou.");
          }
          const ticketPayload = (await ticketResponse.json()) as { ticket?: string };
          if (!ticketPayload.ticket) throw new Error("Ticket de áudio ausente.");
          streamCredential = ticketPayload.ticket;
          if (!iapAttemptIsCurrent()) return;
        } catch (err) {
          if (!iapAttemptIsCurrent()) return;
          setLastError(err instanceof Error ? err.message : "Falha ao obter ticket de áudio.");
          scheduleRetry();
          return;
        }
      }

      let config;
      try {
        config = buildStreamSocketConfig(
          WS_STREAM_BASE,
          sessionId,
          streamCredential!,
          ["microphone"],
        );
      } catch (err) {
        setLastError(
          err instanceof Error
            ? err.message
            : "Configuração inválida para transmissão.",
        );
        return;
      }

      if (!iapAttemptIsCurrent()) return;

      // Open WebSocket connection to native stream gateway before mutating active state
      let ws: WebSocket;
      try {
        ws = new WebSocket(config.url, config.protocols);
      } catch {
        scheduleRetry();
        setLastError("Falha ao abrir conexão com o gateway de áudio.");
        return;
      }

      if (!iapAttemptIsCurrent()) {
        ws.close();
        return;
      }
      const committedSocket = commitIfCurrent(ws, iapAttemptIsCurrent, (stale) => stale.close());
      if (!committedSocket) return;
      ws = committedSocket;

      helloReadySocketRef.current = null;
      activeSessionIdRef.current = sessionId;
      sequenceRef.current = 0;
      sampleOffsetRef.current = 0;
      pcmBufferRef.current = new Float32Array(0);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        if (wsRef.current !== ws || !iapAttemptIsCurrent()) {
          ws.close();
          return;
        }
        streamReconnectDelayRef.current = 1000;
        try {
          ws.send(config.hello);
          helloReadySocketRef.current = ws;
          setIsStreaming(true);
        } catch {
          if (helloReadySocketRef.current === ws) {
            helloReadySocketRef.current = null;
          }
          setIsStreaming(false);
          setLastError("Falha ao anunciar a fonte de áudio ao gateway.");
          ws.close();
          return;
        }

        // Start ping keepalive
        if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
        pingIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, 5000);
      };

      ws.onerror = () => {
        if (wsRef.current !== ws) {
          return;
        }
        if (helloReadySocketRef.current === ws) {
          helloReadySocketRef.current = null;
        }
        setLastError("Erro na conexão com o gateway de áudio.");
      };

      ws.onclose = (event) => {
        if (wsRef.current !== ws) {
          return;
        }
        if (helloReadySocketRef.current === ws) {
          helloReadySocketRef.current = null;
        }
        setIsStreaming(false);
        if (pingIntervalRef.current) {
          clearInterval(pingIntervalRef.current);
          pingIntervalRef.current = null;
        }
        if (runtimeConfig.iap && isIapTerminalClose(event.code, event.reason)) {
          stopStreaming();
          emitIapTerminalAuthEvent();
        } else if (
          runtimeConfig.iap &&
          iapAttemptIsCurrent() &&
          activeSessionIdRef.current === sessionId
        ) {
          // Every retry obtains a new HTTP stream ticket above. Policy
          // logout/kill events are terminal and never enter this branch.
          scheduleRetry();
        }
      };

      // Start audio graph
      try {
        await setupAudioGraph(
          selectedDeviceId,
          attemptGeneration,
          graphAttemptGeneration,
        );
      } catch (err) {
        if (!iapAttemptIsCurrent()) return;
        setLastError(
          err instanceof Error
            ? err.message
            : "Falha ao iniciar captura de áudio do microfone.",
        );
        stopStreaming();
      }
    },
    [selectedDeviceId, setupAudioGraph, stopStreaming],
  );

  useEffect(() => {
    startStreamingRef.current = startStreaming;
    return () => {
      if (startStreamingRef.current === startStreaming) {
        startStreamingRef.current = null;
      }
    };
  }, [startStreaming]);

  // Initial load: enumerate devices & listen for changes
  useEffect(() => {
    void updateDeviceList();

    const handleDeviceChange = () => {
      void updateDeviceList();
    };

    if (navigator.mediaDevices?.addEventListener) {
      navigator.mediaDevices.addEventListener("devicechange", handleDeviceChange);
      return () => {
        navigator.mediaDevices.removeEventListener(
          "devicechange",
          handleDeviceChange,
        );
      };
    }
  }, [updateDeviceList]);

  useEffect(() => {
    const onTerminalAuth = () => stopStreaming();
    window.addEventListener(IAP_AUTH_TERMINAL_EVENT, onTerminalAuth);
    return () => window.removeEventListener(IAP_AUTH_TERMINAL_EVENT, onTerminalAuth);
  }, [stopStreaming]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stopStreaming();
    };
  }, [stopStreaming]);

  return {
    devices,
    selectedDeviceId,
    selectDevice,
    audioLevel,
    isStreaming,
    permissionState,
    requestPermission,
    startStreaming,
    stopStreaming,
    lastError,
  };
}
