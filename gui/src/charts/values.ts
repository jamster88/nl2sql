/**
 * From rows to something drawable.
 *
 * Two facts about the data drive everything here. First, a cell is not
 * necessarily a number even when the column is numeric: Postgres `numeric`
 * crosses JSON as a string so it does not lose precision, so `"719279.97"`
 * and `719279.97` are the same value arriving from two different column
 * types. Second, the chart spec names columns but promises nothing about
 * them -- it was chosen from the shape of the result, and a client that
 * trusts it blindly draws a broken axis the first time a row is null.
 *
 * So every value is coerced and every coercion can fail, and a series that
 * ends up with nothing in it is dropped rather than drawn as a flat line at
 * zero, which would be a claim the data does not make.
 */

import { OTHER_LABEL, SERIES_CAP, SERIES_CAP_ALL_PAIRS } from "./palette";
import type { ChartSpec, ResultTable } from "../api/types";

export interface ChartPoint {
  /** What the x position is called. The category, or the formatted number. */
  label: string;
  /** Position along x: the category index for a band, the value for a scatter. */
  x: number;
  y: number;
  /** Which row of `result` this came from, for highlighting the table. */
  row: number;
}

export interface ChartSeries {
  name: string;
  points: ChartPoint[];
}

export interface ChartData {
  series: ChartSeries[];
  /** Band labels along x. Empty when x is a continuous scale. */
  categories: string[];
  xLabel: string;
  yLabel: string;
  /** True when x is a number to be scaled, false when it is a band. */
  numericX: boolean;
}

/**
 * A cell as a number, or null when it is not one.
 *
 * `Number("")` is 0 and `Number(null)` is 0, which would quietly turn every
 * missing value into a real data point sitting on the axis, so both are
 * rejected before the conversion rather than after it.
 */
export function toNumber(cell: unknown): number | null {
  if (typeof cell === "number") return Number.isFinite(cell) ? cell : null;
  if (typeof cell === "string") {
    const trimmed = cell.trim();
    if (trimmed === "") return null;
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : null;
  }
  if (typeof cell === "boolean") return cell ? 1 : 0;
  return null;
}

/** A cell as a label. `null` is shown as such rather than as an empty gap. */
export function toLabel(cell: unknown): string {
  if (cell === null || cell === undefined) return "—";
  if (typeof cell === "string") return cell;
  if (typeof cell === "number" || typeof cell === "boolean") return String(cell);
  return JSON.stringify(cell);
}

/**
 * A list with something in it.
 *
 * `chartData` refuses a spec with no measures before either shaping function
 * runs, so `measures[0]` below is always there -- but under
 * `noUncheckedIndexedAccess` the compiler cannot know that, and writing
 * `measures[0] ?? ""` to satisfy it would add a fallback that can never be
 * taken and can never be tested. Saying "non-empty" in the type instead
 * moves the check to the one place it actually happens.
 */
type NonEmpty<T> = [T, ...T[]];

function isNonEmpty<T>(items: T[]): items is NonEmpty<T> {
  return items.length > 0;
}

function columnIndex(result: ResultTable, name: string | null): number {
  return name === null ? -1 : result.columns.indexOf(name);
}

function cell(result: ResultTable, row: number, column: number): unknown {
  return result.rows[row]?.[column];
}

/**
 * Turn a result and a spec into series, or `null` when they cannot be drawn.
 *
 * `null` is a normal outcome, not a failure: the spec names columns of a
 * result the server has since truncated, or every value in the measure
 * column is null. The caller falls back to the table, which is always right.
 */
export function chartData(result: ResultTable, spec: ChartSpec): ChartData | null {
  const measures = spec.y.filter((name) => result.columns.includes(name));
  if (!isNonEmpty(measures) || result.rows.length === 0) return null;

  return spec.kind === "scatter"
    ? scatterData(result, spec, measures)
    : bandData(result, spec, measures);
}

/** x is a band: one slot per row, or per distinct value of `series`. */
function bandData(result: ResultTable, spec: ChartSpec, measures: NonEmpty<string>): ChartData | null {
  const xIndex = columnIndex(result, spec.x);
  const seriesIndex = columnIndex(result, spec.series);

  // Without an x column every row is its own slot, numbered from one. That
  // is what the pipeline produces for a bare list of measures, and "1, 2,
  // 3" is a more honest axis than inventing labels for it.
  //
  // The bands are carried as entries rather than as a list of labels beside
  // a lookup table: the two would have to be kept in step by hand, and a
  // lookup that cannot miss still has to be written as though it could.
  const grouped = new Map<string, number[]>();
  result.rows.forEach((_, row) => {
    const label = xIndex >= 0 ? toLabel(cell(result, row, xIndex)) : String(row + 1);
    const rows = grouped.get(label);
    if (rows) rows.push(row);
    else grouped.set(label, [row]);
  });
  const bands: Band[] = [...grouped];

  const series =
    seriesIndex >= 0
      ? pivotBySeries(result, seriesIndex, measures[0], bands)
      : measures.map((name) => ({
          name,
          points: pointsFor(result, columnIndex(result, name), bands),
        }));

  const drawn = capped(series, false);
  if (drawn.length === 0) return null;
  return {
    series: drawn,
    categories: bands.map(([label]) => label),
    xLabel: spec.x ?? "",
    // Named only when there is one measure to name. Two measures share the
    // axis, and labelling it with the first would misattribute the second.
    yLabel: measures.length === 1 ? measures[0] : "",
    numericX: false,
  };
}

/** One slot on the x axis: its label, and the rows that landed in it. */
type Band = [label: string, rows: number[]];

function pointsFor(result: ResultTable, measure: number, bands: readonly Band[]): ChartPoint[] {
  const points: ChartPoint[] = [];
  bands.forEach(([label, rows], x) => {
    for (const row of rows) {
      const value = toNumber(cell(result, row, measure));
      if (value !== null) points.push({ label, x, y: value, row });
    }
  });
  return points;
}

/** One series per distinct value of the `series` column. */
function pivotBySeries(
  result: ResultTable,
  seriesIndex: number,
  measure: string,
  bands: readonly Band[],
): ChartSeries[] {
  const measureIndex = columnIndex(result, measure);
  const byName = new Map<string, ChartPoint[]>();
  bands.forEach(([label, rows], x) => {
    for (const row of rows) {
      const value = toNumber(cell(result, row, measureIndex));
      if (value === null) continue;
      const name = toLabel(cell(result, row, seriesIndex));
      const points = byName.get(name) ?? [];
      points.push({ label, x, y: value, row });
      byName.set(name, points);
    }
  });
  return [...byName].map(([name, points]) => ({ name, points }));
}

/** x and y are both numbers. */
function scatterData(result: ResultTable, spec: ChartSpec, measures: NonEmpty<string>): ChartData | null {
  // Taken apart rather than tested with `columnIndex`, so that "the spec
  // named no x" and "the spec named an x the result does not have" stay two
  // distinct outcomes instead of collapsing into one -1.
  const xName = spec.x;
  if (xName === null) return null;
  const xIndex = result.columns.indexOf(xName);
  if (xIndex < 0) return null;

  const measure = measures[0];
  const yIndex = result.columns.indexOf(measure);
  const seriesIndex = columnIndex(result, spec.series);
  const byName = new Map<string, ChartPoint[]>();
  result.rows.forEach((_, row) => {
    const x = toNumber(cell(result, row, xIndex));
    const y = toNumber(cell(result, row, yIndex));
    if (x === null || y === null) return;
    const name = seriesIndex >= 0 ? toLabel(cell(result, row, seriesIndex)) : measure;
    const points = byName.get(name) ?? [];
    points.push({ label: `${formatValue(x)}, ${formatValue(y)}`, x, y, row });
    byName.set(name, points);
  });

  const drawn = capped(
    [...byName].map(([name, points]) => ({ name, points })),
    true,
  );
  if (drawn.length === 0) return null;
  return {
    series: drawn,
    categories: [],
    xLabel: xName,
    yLabel: measure,
    numericX: true,
  };
}

/**
 * Drop empty series, then fold everything past the colour cap into one.
 *
 * Folding rather than truncating: a chart that silently omits the ninth
 * region is a chart that is wrong about the total, while one that shows an
 * "Other" is only less detailed.
 */
function capped(series: ChartSeries[], allPairs: boolean): ChartSeries[] {
  const populated = series.filter((entry) => entry.points.length > 0);
  const cap = allPairs ? SERIES_CAP_ALL_PAIRS : SERIES_CAP;
  if (populated.length <= cap) return populated;

  const kept = populated.slice(0, cap - 1);
  const folded = populated.slice(cap - 1).flatMap((entry) => entry.points);
  return [...kept, { name: OTHER_LABEL, points: folded }];
}

/**
 * A number, short enough for an axis and honest enough for a total.
 *
 * Thousands separators below a million, then a suffix, because an axis
 * reading 1,250,000,000 is an axis nobody reads. Small magnitudes keep
 * their decimals: a 0.81 match score rounded to 1 would be a lie.
 */
export function formatValue(value: number): string {
  const magnitude = Math.abs(value);
  if (magnitude >= 1e9) return `${trim(value / 1e9)}B`;
  if (magnitude >= 1e6) return `${trim(value / 1e6)}M`;
  if (magnitude >= 1000) return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (magnitude >= 1 || magnitude === 0) return trim(value);
  return trim(value, 4);
}

function trim(value: number, digits = 2): string {
  return String(Number(value.toFixed(digits)));
}
