/**
 * The marks themselves.
 *
 * The specifics here are the mark spec, and each one has a reason:
 *
 * * **Bars are three-quarters of their slot** and their top corners are
 *   rounded by 4px, anchored to the baseline. A bar filling its slot reads
 *   as a region; a bar with all four corners rounded reads as floating.
 * * **Adjacent fills are separated by 2px of surface**, so two bars of
 *   similar colour are still two bars.
 * * **Lines are 2px** and markers are at least 8px across, which is the
 *   smallest a pointer reliably hits and the smallest a printed dot stays a
 *   dot.
 * * **Overlapping marks carry a 2px surface ring**, so a scatter with
 *   coincident points still shows two.
 * * **Labels are selective.** A number on every point is a table drawn
 *   badly; the labels go where the reader looks -- the end of a line, the
 *   top of a bar when there are few enough to read.
 */

import { Fragment } from "react";

import { seriesVar } from "./palette";
import { formatValue, type ChartData, type ChartPoint } from "./values";
import { truncate } from "./Frame";
import type { BandScale, LinearScale } from "./scale";

export interface Hover {
  seriesName: string;
  seriesIndex: number;
  point: ChartPoint;
  /** Where the tooltip should point, in the plot group's coordinates. */
  cx: number;
  cy: number;
}

export interface MarkProps {
  data: ChartData;
  y: LinearScale;
  onHover: (hover: Hover | null) => void;
}

export interface BandMarkProps extends MarkProps {
  band: BandScale;
}

export interface PointMarkProps extends MarkProps {
  x: LinearScale;
}

/** The gap that keeps two fills from merging into one shape. */
const SURFACE_GAP = 2;
const CORNER = 4;
/** Above this many bars, a number on each one is noise rather than help. */
const MAX_BAR_LABELS = 12;
/** Past four series, direct labels collide more than they clarify. */
const MAX_DIRECT_LABELS = 4;

/**
 * A bar with its top corners rounded and its base square on the axis.
 *
 * Written as a path rather than a `rect` with `rx` because `rx` rounds all
 * four corners, which lifts the bar off its own baseline.
 */
export function barPath(left: number, width: number, top: number, base: number): string {
  const height = Math.abs(base - top);
  const radius = Math.min(CORNER, width / 2, height);
  const right = left + width;
  // A bar below the axis is the same shape reflected, so the rounded end is
  // always the end away from zero.
  const sign = top <= base ? 1 : -1;
  const capped = top + sign * radius;
  return [
    `M${left},${base}`,
    `L${left},${capped}`,
    `Q${left},${top} ${left + radius},${top}`,
    `L${right - radius},${top}`,
    `Q${right},${top} ${right},${capped}`,
    `L${right},${base}`,
    "Z",
  ].join("");
}

export function BarMarks({ data, band, y, onHover }: BandMarkProps) {
  const count = data.series.length;
  const slot = band.bandwidth / count;
  const width = Math.max(slot - (count > 1 ? SURFACE_GAP : 0), 1);
  const base = y(Math.max(y.domain[0], 0));
  const labelled = count === 1 && data.categories.length <= MAX_BAR_LABELS;

  return (
    <>
      {data.series.map((series, index) => (
        <Fragment key={series.name}>
          {series.points.map((point) => {
            const left = band(point.x) + index * slot + (count > 1 ? SURFACE_GAP / 2 : 0);
            const top = y(point.y);
            return (
              <Fragment key={`${point.row}-${point.x}`}>
                <path
                  className="chart-bar"
                  d={barPath(left, width, top, base)}
                  fill={seriesVar(index)}
                  tabIndex={0}
                  role="graphics-symbol"
                  aria-label={`${series.name} ${point.label}: ${formatValue(point.y)}`}
                  onMouseEnter={() =>
                    onHover({ seriesName: series.name, seriesIndex: index, point, cx: left + width / 2, cy: top })
                  }
                  onFocus={() =>
                    onHover({ seriesName: series.name, seriesIndex: index, point, cx: left + width / 2, cy: top })
                  }
                  onMouseLeave={() => onHover(null)}
                  onBlur={() => onHover(null)}
                />
                {labelled && (
                  <text
                    className="chart-value"
                    x={left + width / 2}
                    y={top + (point.y >= 0 ? -6 : 14)}
                    textAnchor="middle"
                  >
                    {formatValue(point.y)}
                  </text>
                )}
              </Fragment>
            );
          })}
        </Fragment>
      ))}
    </>
  );
}

export function LineMarks({ data, band, y, onHover }: BandMarkProps) {
  const direct = data.series.length <= MAX_DIRECT_LABELS && data.series.length > 1;
  return (
    <>
      {data.series.map((series, index) => {
        const path = series.points
          .map((point, at) => `${at === 0 ? "M" : "L"}${band.center(point.x)},${y(point.y)}`)
          .join("");
        const last = series.points[series.points.length - 1];
        return (
          <Fragment key={series.name}>
            <path className="chart-line" d={path} stroke={seriesVar(index)} fill="none" />
            {series.points.map((point) => (
              <circle
                key={`${point.row}-${point.x}`}
                className="chart-dot"
                cx={band.center(point.x)}
                cy={y(point.y)}
                r={4}
                fill={seriesVar(index)}
                tabIndex={0}
                role="graphics-symbol"
                aria-label={`${series.name} ${point.label}: ${formatValue(point.y)}`}
                onMouseEnter={() =>
                  onHover({
                    seriesName: series.name,
                    seriesIndex: index,
                    point,
                    cx: band.center(point.x),
                    cy: y(point.y),
                  })
                }
                onFocus={() =>
                  onHover({
                    seriesName: series.name,
                    seriesIndex: index,
                    point,
                    cx: band.center(point.x),
                    cy: y(point.y),
                  })
                }
                onMouseLeave={() => onHover(null)}
                onBlur={() => onHover(null)}
              />
            ))}
            {direct && last && (
              <text
                className="chart-value"
                x={band.center(last.x) + 8}
                y={y(last.y)}
                dy="0.32em"
                textAnchor="start"
              >
                {truncate(series.name, 12)}
              </text>
            )}
          </Fragment>
        );
      })}
    </>
  );
}

export function ScatterMarks({ data, x, y, onHover }: PointMarkProps) {
  return (
    <>
      {data.series.map((series, index) =>
        series.points.map((point) => (
          <circle
            key={`${series.name}-${point.row}`}
            className="chart-point"
            cx={x(point.x)}
            cy={y(point.y)}
            r={5}
            fill={seriesVar(index)}
            tabIndex={0}
            role="graphics-symbol"
            aria-label={`${series.name} ${point.label}`}
            onMouseEnter={() =>
              onHover({ seriesName: series.name, seriesIndex: index, point, cx: x(point.x), cy: y(point.y) })
            }
            onFocus={() =>
              onHover({ seriesName: series.name, seriesIndex: index, point, cx: x(point.x), cy: y(point.y) })
            }
            onMouseLeave={() => onHover(null)}
            onBlur={() => onHover(null)}
          />
        )),
      )}
    </>
  );
}
