/**
 * Scales and ticks.
 *
 * Small enough to write, and writing it is cheaper than the alternative:
 * every charting library would have to be bent back towards the mark specs
 * this application follows -- thin marks, rounded data ends, a surface gap
 * between neighbours, labels on some points and not all -- and bending a
 * library's defaults is more code than a linear scale, less obvious, and
 * pinned to that library's next major version.
 */

/** Maps a value in `domain` onto a position in `range`. */
export interface LinearScale {
  (value: number): number;
  domain: readonly [number, number];
  range: readonly [number, number];
  ticks: number[];
}

/**
 * Round tick steps: 1, 2, 5 and their powers of ten.
 *
 * Any other step produces axis labels like 3.7 and 7.4, which a reader has
 * to do arithmetic on before the chart means anything.
 */
export function niceStep(span: number, count: number): number {
  if (span <= 0 || count <= 0) return 1;
  const rough = span / count;
  const power = 10 ** Math.floor(Math.log10(rough));
  const normalised = rough / power;
  const step = normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 5 ? 5 : 10;
  return step * power;
}

export function ticksFor(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  const step = niceStep(max - min, count);
  const first = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  // The epsilon absorbs the float error that otherwise drops the last tick
  // on a domain like 0..0.3, where the accumulated step overshoots by 1e-17.
  for (let tick = first; tick <= max + step * 1e-9; tick += step) {
    ticks.push(Number(tick.toPrecision(12)));
  }
  return ticks;
}

export function linearScale(
  domain: readonly [number, number],
  range: readonly [number, number],
  tickCount = 5,
): LinearScale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  // A domain of zero width would divide by zero; putting the single value in
  // the middle of the range is the only sensible place for it.
  const span = d1 - d0;
  const scale = ((value: number) =>
    span === 0 ? (r0 + r1) / 2 : r0 + ((value - d0) / span) * (r1 - r0)) as LinearScale;
  scale.domain = domain;
  scale.range = range;
  scale.ticks = ticksFor(d0, d1, tickCount);
  return scale;
}

/**
 * The y domain for a set of values.
 *
 * Anchored at zero whenever the data does not cross it, because a bar whose
 * baseline is not zero misstates every comparison it is drawn to make. The
 * top is padded to the next nice step so the tallest mark is not flush with
 * the frame.
 */
export function valueDomain(values: readonly number[]): [number, number] {
  if (values.length === 0) return [0, 1];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return min === 0 ? [0, 1] : min > 0 ? [0, max * 1.2] : [min * 1.2, 0];
  const step = niceStep(max - min, 5);
  return [min < 0 ? Math.floor(min / step) * step : 0, Math.ceil(max / step) * step];
}

/** A domain for a continuous x, padded by 5% so no mark sits on the frame. */
export function extentDomain(values: readonly number[]): [number, number] {
  if (values.length === 0) return [0, 1];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return [min - 1, max + 1];
  const pad = (max - min) * 0.05;
  return [min - pad, max + pad];
}

export interface BandScale {
  /** Left edge of band `index`. */
  (index: number): number;
  bandwidth: number;
  step: number;
  /** Centre of band `index`, which is where a point or a label goes. */
  center: (index: number) => number;
}

/**
 * Evenly spaced slots across `width`, with a gap between them.
 *
 * `padding` is the share of each step given to the gap. 0.25 leaves a bar
 * three-quarters of its slot wide, which reads as a bar rather than as a
 * filled region -- the mark spec's "thin marks" rule applied to a bar.
 */
export function bandScale(count: number, width: number, padding = 0.25): BandScale {
  const step = count > 0 ? width / count : width;
  const bandwidth = Math.max(step * (1 - padding), 1);
  const offset = (step - bandwidth) / 2;
  const scale = ((index: number) => index * step + offset) as BandScale;
  scale.bandwidth = bandwidth;
  scale.step = step;
  scale.center = (index: number) => index * step + step / 2;
  return scale;
}

/**
 * Which band labels to draw.
 *
 * Every label at 40 categories is a black smear, and rotating them to fit is
 * how a chart ends up unreadable in two directions instead of one. Thinning
 * keeps the first and last, which is what a reader looks for.
 */
export function labelStride(count: number, width: number, minPixels = 56): number {
  if (count <= 1) return 1;
  const perLabel = width / count;
  return perLabel >= minPixels ? 1 : Math.ceil(minPixels / Math.max(perLabel, 1));
}
