// Reference telemetry traces for the Monaco Turn 6–8 window, reconstructed
// from the shapes drawn in docs/assets/ui-reference.png. Distances in metres.

type Key = [number, number];

function interpolate(keys: Key[], x: number): number {
  if (x <= keys[0][0]) return keys[0][1];
  for (let i = 1; i < keys.length; i += 1) {
    const [x1, y1] = keys[i];
    const [x0, y0] = keys[i - 1];
    if (x <= x1) {
      const t = (x - x0) / (x1 - x0);
      const eased = t * t * (3 - 2 * t);
      return y0 + (y1 - y0) * eased;
    }
  }
  return keys[keys.length - 1][1];
}

function shift(keys: Key[], dx: number, dy = 0): Key[] {
  return keys.map(([x, y]) => [x + dx, y + dy]);
}

const speedTarget: Key[] = [
  [2300, 248], [2350, 252], [2372, 236], [2405, 140], [2440, 134], [2470, 172],
  [2495, 168], [2520, 104], [2545, 30], [2575, 48], [2600, 38], [2622, 62],
  [2650, 132], [2690, 186], [2730, 206], [2760, 210], [2788, 150], [2810, 70],
  [2835, 58], [2862, 118], [2900, 196], [2950, 238], [3000, 246], [3060, 240],
];

const speedReference: Key[] = [
  [2300, 250], [2352, 254], [2376, 232], [2408, 146], [2440, 140], [2468, 176],
  [2492, 170], [2516, 110], [2540, 42], [2570, 60], [2596, 52], [2616, 82],
  [2645, 146], [2685, 196], [2722, 214], [2756, 214], [2784, 152], [2806, 74],
  [2830, 64], [2858, 126], [2896, 200], [2946, 240], [2998, 248], [3060, 242],
];

const brakeZones = (offset: number): Array<[number, number, number]> => [
  [2378 + offset, 2402 + offset, 42],
  [2504 + offset, 2532 + offset, 44],
  [2528 + offset, 2540 + offset, 30],
  [2790 + offset, 2812 + offset, 45],
];

const throttleOff = (offset: number): Array<[number, number]> => [
  [2376 + offset, 2440 + offset],
  [2500 + offset, 2600 + offset],
  [2612 + offset, 2632 + offset],
  [2786 + offset, 2846 + offset],
  [2972 + offset, 2996 + offset],
];

const steeringTarget: Key[] = [
  [2300, -60], [2350, -62], [2385, -72], [2410, -96], [2430, -82], [2448, -40],
  [2462, -28], [2480, -58], [2500, -60], [2522, -78], [2545, -98], [2565, -74],
  [2585, -56], [2605, -92], [2620, -66], [2640, -42], [2660, -60], [2680, -36],
  [2705, -48], [2730, -58], [2760, -66], [2790, -56], [2810, -34], [2830, -60],
  [2860, -64], [2900, -60], [2950, -76], [2990, -62], [3060, -58],
];

function throttle(x: number, offset: number): number {
  for (const [start, end] of throttleOff(offset)) {
    if (x >= start && x <= end) {
      const edge = Math.min(x - start, end - x);
      return edge < 6 ? 100 - (edge / 6) * 100 : 0;
    }
  }
  return 100;
}

function brake(x: number, offset: number): number {
  for (const [start, end, peak] of brakeZones(offset)) {
    if (x >= start && x <= end) {
      const edge = Math.min(x - start, end - x);
      return edge < 5 ? (edge / 5) * peak : peak;
    }
  }
  return 0;
}

export type Trace = Array<[number, number]>;
export type ChannelTraces = { target: Trace; reference: Trace };

export function referenceTraces(from: number, to: number, step = 4) {
  const xs: number[] = [];
  for (let x = from; x <= to; x += step) xs.push(x);
  const steeringReference = shift(steeringTarget, -6, 4);
  return {
    speed: {
      target: xs.map((x) => [x, interpolate(speedTarget, x)]),
      reference: xs.map((x) => [x, interpolate(speedReference, x)]),
    },
    throttle: {
      target: xs.map((x) => [x, throttle(x, 0)]),
      reference: xs.map((x) => [x, throttle(x, -8)]),
    },
    brake: {
      target: xs.map((x) => [x, brake(x, 0)]),
      reference: xs.map((x) => [x, brake(x, -6)]),
    },
    steering: {
      target: xs.map((x) => [x, interpolate(steeringTarget, x)]),
      reference: xs.map((x) => [x, interpolate(steeringReference, x)]),
    },
  } satisfies Record<string, ChannelTraces>;
}
