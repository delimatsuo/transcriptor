export function admissionIsCurrent(
  signal: AbortSignal,
  generation: number,
  currentGeneration: number,
  currentUid: string | undefined,
  admittedUid: string,
): boolean {
  return (
    !signal.aborted &&
    generation === currentGeneration &&
    currentUid === admittedUid
  );
}
