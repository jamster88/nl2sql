/**
 * Everything around the marks: the grid, the two axes, the legend.
 *
 * Kept apart from the marks because the rules here are the same whatever is
 * being drawn -- a recessive grid, horizontal lines only, labels thinned
 * rather than rotated, no chart junk -- and repeating them per chart type is
 * how three charts end up with three different axes.
 */

import type { ReactNode } from "react";

import { formatValue } from "./values";
import { labelStride, type BandScale, type LinearScale } from "./scale";
import { seriesVar } from "./palette";

export interface Margin {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export const MARGIN: Margin = { top: 16, right: 24, bottom: 44, left: 68 };
export const PLOT_HEIGHT = 300;

export interface FrameProps {
  width: number;
  height: number;
  y: LinearScale;
  /** Band labels along the bottom, for a categorical x. */
  categories?: string[];
  band?: BandScale;
  /** A continuous x instead, for a scatter. */
  x?: LinearScale;
  xLabel: string;
  yLabel: string;
  children: ReactNode;
}

export function Frame({
  width,
  height,
  y,
  categories,
  band,
  x,
  xLabel,
  yLabel,
  children,
}: FrameProps) {
  const plotWidth = Math.max(width - MARGIN.left - MARGIN.right, 1);
  const stride = categories ? labelStride(categories.length, plotWidth) : 1;

  return (
    <svg
      className="chart-svg"
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${yLabel || "value"} by ${xLabel || "row"}`}
    >
      <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
        {/* The grid is horizontal only: a y value is read across, and a
            second set of lines going the other way adds nothing to read. */}
        {y.ticks.map((tick) => (
          <g key={tick}>
            <line className="chart-grid" x1={0} x2={plotWidth} y1={y(tick)} y2={y(tick)} />
            <text className="chart-tick" x={-10} y={y(tick)} dy="0.32em" textAnchor="end">
              {formatValue(tick)}
            </text>
          </g>
        ))}

        {/* Zero is drawn darker than the grid when the data crosses it,
            because a negative value means nothing without it. */}
        {y.domain[0] < 0 && (
          <line className="chart-axis" x1={0} x2={plotWidth} y1={y(0)} y2={y(0)} />
        )}

        {children}

        {categories && band
          ? categories.map((label, index) =>
              index % stride === 0 ? (
                <text
                  key={`${label}-${index}`}
                  className="chart-tick"
                  x={band.center(index)}
                  y={y.range[0] + 20}
                  textAnchor="middle"
                >
                  {truncate(label)}
                </text>
              ) : null,
            )
          : x?.ticks.map((tick) => (
              <text
                key={tick}
                className="chart-tick"
                x={x(tick)}
                y={y.range[0] + 20}
                textAnchor="middle"
              >
                {formatValue(tick)}
              </text>
            ))}

        {xLabel && (
          <text
            className="chart-axis-label"
            x={plotWidth / 2}
            y={y.range[0] + 40}
            textAnchor="middle"
          >
            {xLabel}
          </text>
        )}
        {yLabel && (
          <text
            className="chart-axis-label"
            transform={`translate(${-MARGIN.left + 14},${y.range[0] / 2}) rotate(-90)`}
            textAnchor="middle"
          >
            {yLabel}
          </text>
        )}
      </g>
    </svg>
  );
}

/**
 * An axis label long enough to collide with its neighbours is cut, not
 * rotated. The trim matters: cutting mid-word often leaves a trailing space,
 * and "Frozen Foods …" reads as a missing word rather than a truncation.
 */
export function truncate(label: string, max = 14): string {
  return label.length > max ? `${label.slice(0, max - 1).trimEnd()}…` : label;
}

export interface LegendProps {
  names: readonly string[];
}

/**
 * Identity, never by colour alone.
 *
 * Present whenever there are two or more series. One series needs none --
 * the axis already names it -- and a legend box for it is furniture.
 */
export function Legend({ names }: LegendProps) {
  if (names.length < 2) return null;
  return (
    <ul className="chart-legend">
      {names.map((name, index) => (
        <li key={name}>
          <span className="chart-swatch" style={{ background: seriesVar(index) }} aria-hidden="true" />
          {name}
        </li>
      ))}
    </ul>
  );
}
