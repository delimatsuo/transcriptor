/**
 * Build the native audio stream WebSocket URL for a session.
 *
 * The native stream gateway (`/api/stream/native/{session_id}`) requires a
 * `stream_key` query parameter minted by `POST /api/sessions` — connections
 * without a matching key are rejected at the WebSocket handshake (close code
 * 1008). When no key is supplied, the base session URL is returned
 * unchanged, e.g. for callers that predate stream-key auth.
 */
export function buildStreamUrl(
  base: string,
  sessionId: string,
  streamKey?: string,
): string {
  const url = `${base}/${sessionId}`;
  if (!streamKey) {
    return url;
  }
  return `${url}?stream_key=${encodeURIComponent(streamKey)}`;
}
