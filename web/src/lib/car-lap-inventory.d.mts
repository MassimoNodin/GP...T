import type { CarLapInventory } from "./api";

export function isMatchingCarLapInventory(
  value: unknown,
  runId: string,
  sessionUid: string,
  carIndex: number,
  offset: number,
): value is CarLapInventory;
