"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  encodeAudioFrame,
  float32ToInt16,
  resampleTo16k,
  type FrameMetadata,
} from "@/lib/browserPcmEncoder";
import { buildStreamSocketConfig } from "@/lib/streamUrl";

const WS_STREAM_BASE =
  process.env.NEXT_PUBLIC_WS_STREAM_URL ||
  "ws://127.0.0.1:8000/api/stream/native";
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
  const animFrameRef = useRef<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
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
    async (deviceId: string) => {
      // Teardown previous nodes
      if (processorNodeRef.current) {
        processorNodeRef.current.disconnect();
        processorNodeRef.current = null;
      }
      if (sourceNodeRef.current) {
        sourceNodeRef.current.disconnect();
        sourceNodeRef.current = null;
      }
      if (mediaStreamRef.current) {
        mediaStreamRef.current.getTracks().forEach((t) => t.stop());
        mediaStreamRef.current = null;
      }

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

      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      mediaStreamRef.current = stream;

      const AudioContextClass =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext })
          .webkitAudioContext;
      const ctx = audioContextRef.current || new AudioContextClass();
      if (ctx.state === "suspended") {
        await ctx.resume();
      }
      audioContextRef.current = ctx;

      const sourceNode = ctx.createMediaStreamSource(stream);
      sourceNodeRef.current = sourceNode;

      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.5;
      analyserNodeRef.current = analyser;
      sourceNode.connect(analyser);
      startLevelMeter(analyser);

      // ScriptProcessor downsamples and emits 50ms chunks (800 samples at 16kHz)
      const bufferSize = 2048;
      const processor = ctx.createScriptProcessor(bufferSize, 1, 1);
      processorNodeRef.current = processor;

      processor.onaudioprocess = (e: AudioProcessingEvent) => {
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
        const resampled = resampleTo16k(inputData, ctx.sampleRate, 16000);

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
      const silentGain = ctx.createGain();
      silentGain.gain.value = 0;
      processor.connect(silentGain);
      silentGain.connect(ctx.destination);
    },
    [startLevelMeter],
  );

  // Switch microphone device on the fly
  const selectDevice = useCallback(
    async (deviceId: string) => {
      setSelectedDeviceId(deviceId);
      if (typeof window !== "undefined") {
        localStorage.setItem(STORAGE_KEY_MIC, deviceId);
      }
      if (isStreaming || mediaStreamRef.current) {
        try {
          await setupAudioGraph(deviceId);
        } catch (err) {
          setLastError(
            err instanceof Error ? err.message : "Falha ao trocar de microfone.",
          );
        }
      }
    },
    [isStreaming, setupAudioGraph],
  );

  // Stop streaming & release resources
  const stopStreaming = useCallback(() => {
    helloReadySocketRef.current = null;
    activeSessionIdRef.current = null;
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
      if (!streamKey) {
        setLastError("Chave do fluxo de áudio ausente.");
        return;
      }

      let config;
      try {
        config = buildStreamSocketConfig(
          WS_STREAM_BASE,
          sessionId,
          streamKey,
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

      // Open WebSocket connection to native stream gateway before mutating active state
      let ws: WebSocket;
      try {
        ws = new WebSocket(config.url, config.protocols);
      } catch {
        setLastError("Falha ao abrir conexão com o gateway de áudio.");
        return;
      }

      helloReadySocketRef.current = null;
      activeSessionIdRef.current = sessionId;
      sequenceRef.current = 0;
      sampleOffsetRef.current = 0;
      pcmBufferRef.current = new Float32Array(0);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => {
        if (wsRef.current !== ws) {
          return;
        }
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

      ws.onclose = () => {
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
      };

      // Start audio graph
      try {
        await setupAudioGraph(selectedDeviceId);
      } catch (err) {
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
