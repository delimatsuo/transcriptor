export type TerminalStopStatus = "completed" | "incomplete";

export interface StopOutcome {
  status: TerminalStopStatus;
  warning: string | null;
}

interface StopResponse {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
}

export type StopFetch = (
  input: string,
  init: { method: "POST"; headers?: HeadersInit },
) => Promise<StopResponse>;

const INCOMPLETE_WARNING =
  "Transcrição incompleta: revise o final da entrevista. Nenhum relatório final foi gerado.";

export async function requestSessionStop(
  fetcher: StopFetch,
  url: string,
  stopCapability?: string | null,
): Promise<StopOutcome> {
  const response = await fetcher(url, {
    method: "POST",
    ...(stopCapability
      ? { headers: { "X-TARS-Stop-Capability": stopCapability } }
      : {}),
  });
  if (!response.ok) {
    throw new Error(`stop failed (${response.status})`);
  }

  const result = (await response.json()) as { status?: unknown };
  if (result.status !== "completed" && result.status !== "incomplete") {
    throw new Error("stop returned no terminal status");
  }

  return {
    status: result.status,
    warning: result.status === "incomplete" ? INCOMPLETE_WARNING : null,
  };
}
