/** Deep link that the TarsCompanion menu bar app registers (see companion/native-macos). */
export function buildJoinLink(
  sessionId: string,
  streamKey: string,
): string {
  const params = [
    `session=${encodeURIComponent(sessionId)}`,
    `key=${encodeURIComponent(streamKey)}`,
  ];
  return `tars-companion://join?${params.join("&")}`;
}
