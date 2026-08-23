/**
 * Configuration and wire contract helpers for the native stream WebSocket gateway.
 */

export type NativeStreamSource = "microphone" | "system_audio";

export interface NativeStreamSocketConfig {
  url: string;
  protocols: [string, string];
  hello: string;
}

export function buildStreamSocketConfig(
  base: string,
  sessionId: string,
  streamKey: string,
  sources: NativeStreamSource[],
): NativeStreamSocketConfig {
  if (!streamKey || typeof streamKey !== "string" || streamKey.trim() === "") {
    throw new Error("Chave do fluxo de áudio ausente ou inválida.");
  }
  if (!Array.isArray(sources) || sources.length === 0) {
    throw new Error("Nenhuma fonte de áudio especificada.");
  }
  for (const src of sources) {
    if (src !== "microphone" && src !== "system_audio") {
      throw new Error(`Fonte de áudio desconhecida: ${String(src)}`);
    }
  }

  const uniqueSources = new Set(sources);
  const canonicalSources: NativeStreamSource[] = [];
  if (uniqueSources.has("microphone")) {
    canonicalSources.push("microphone");
  }
  if (uniqueSources.has("system_audio")) {
    canonicalSources.push("system_audio");
  }

  const url = `${base}/${sessionId}`;
  const protocols: [string, string] = ["tars-stream", streamKey];
  const hello = JSON.stringify({
    type: "hello",
    sources: canonicalSources,
  });

  return {
    url,
    protocols,
    hello,
  };
}
