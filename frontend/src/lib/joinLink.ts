/** Deep link that the TarsCompanion menu bar app registers (see companion/native-macos). */
export function buildJoinLink(
  sessionId: string,
  streamKey: string,
  gatewayBase?: string,
): string {
  const params = [
    `session=${encodeURIComponent(sessionId)}`,
    `key=${encodeURIComponent(streamKey)}`,
  ];
  if (gatewayBase) params.push(`gateway=${encodeURIComponent(gatewayBase)}`);
  return `tars-companion://join?${params.join("&")}`;
}
