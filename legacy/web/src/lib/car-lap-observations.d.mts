import type { CarLapObservationPage } from "./api";

export function hasUnambiguousCarLapObservationScope(scope: {
  runIds: string[];
  sessionUids: string[];
  carIndexes: string[];
  attemptKeys: string[];
  observationOffsets: string[];
}): boolean;

export function isMatchingCarLapObservationPage(
  value: unknown,
  runId: string,
  sessionUid: string,
  carIndex: number,
  attemptKey: string,
  offset: number,
): value is CarLapObservationPage;
