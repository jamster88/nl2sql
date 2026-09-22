/**
 * The chart, dispatched on what the pipeline asked for.
 *
 * The agent's Visual Formatter already decided the form from the shape of
 * the result -- one number is a headline, a category against a measure is a
 * bar, a date against a measure is a line, two measures are a scatter -- so
 * this does not decide again. It renders what was asked for and returns
 * nothing when the request cannot be honoured, which is a normal outcome:
 * `kind: "table"` means the rows are the presentation, and the table below
 * the chart is always there either way.
 *
 * That table is also what discharges the contrast rule the palette carries:
 * three light-mode series colours sit below 3:1 against the surface, which
 * is allowed only where the values are also readable as text.
 */

import { useId, useState } from "react";

import type { ChartSpec, ResultTable } from "../api/types";
import { Frame, Legend, MARGIN, PLOT_HEIGHT } from "./Frame";
import { BarMarks, LineMarks, ScatterMarks, type Hover } from "./Plot";
import { bandScale, extentDomain, linearScale, valueDomain } from "./scale";
import { chartData, formatValue, toNumber, type ChartData } from "./values";

export interface ChartProps {
  result: ResultTable;
  spec: ChartSpec;
}

export function Chart({ result, spec }: ChartProps) {
  if (spec.kind === "scalar") return <ScalarTile result={result} spec={spec} />;
  if (spec.kind === "table") return null;
  const data = chartData(result, spec);
  if (data === null) return null;
  return <PlotChart data={data} kind={spec.kind} />;
}

/**
 * One number, drawn as one number.
 *
 * A single value is not a chart and drawing it as a bar of one is the most
 * common way to make a result harder to read than the sentence above it.
 */
export function ScalarTile({ result, spec }: ChartProps) {
  const column = spec.y[0] ?? result.columns[0] ?? "";
  const index = result.columns.indexOf(column);
  const raw = result.rows[0]?.[index];
  const value = toNumber(raw);
  if (value === null) return null;
  return (
    <figure className="chart-scalar">
      <div className="chart-scalar-value">{formatValue(value)}</div>
      <figcaption className="chart-scalar-label">{column}</figcaption>
    </figure>
  );
}

interface PlotChartProps {
  data: ChartData;
  kind: ChartSpec["kind"];
}

function PlotChart({ data, kind }: PlotChartProps) {
  const [hover, setHover] = useState<Hover | null>(null);
  const tooltipId = useId();

  // A fixed drawing width with `viewBox` scaling, rather than re-measuring:
  // the SVG scales to its container on its own, so the only thing a measured
  // width would change is the label thinning, and a stable width keeps the
  // thinning decision from flickering as a panel animates open.
  const width = 720;
  const plotWidth = width - MARGIN.left - MARGIN.right;
  const plotHeight = PLOT_HEIGHT - MARGIN.top - MARGIN.bottom;

  const values = data.series.flatMap((series) => series.points.map((point) => point.y));
  const y = linearScale(valueDomain(values), [plotHeight, 0]);

  const band = bandScale(data.categories.length, plotWidth, kind === "line" ? 0 : 0.25);
  const xs = data.series.flatMap((series) => series.points.map((point) => point.x));
  const x = linearScale(extentDomain(xs), [0, plotWidth]);

  const marks =
    kind === "scatter" ? (
      <ScatterMarks data={data} x={x} y={y} onHover={setHover} />
    ) : kind === "line" ? (
      <LineMarks data={data} band={band} y={y} onHover={setHover} />
    ) : (
      <BarMarks data={data} band={band} y={y} onHover={setHover} />
    );

  return (
    <div className="chart">
      <Frame
        width={width}
        height={PLOT_HEIGHT}
        y={y}
        xLabel={data.xLabel}
        yLabel={data.yLabel}
        {...(kind === "scatter" ? { x } : { categories: data.categories, band })}
      >
        {marks}
      </Frame>
      {hover && (
        <div
          className="chart-tooltip"
          id={tooltipId}
          role="status"
          style={{
            left: `${((hover.cx + MARGIN.left) / width) * 100}%`,
            top: `${((hover.cy + MARGIN.top) / PLOT_HEIGHT) * 100}%`,
          }}
        >
          <strong>{hover.point.label}</strong>
          <span>
            {data.series.length > 1 ? `${hover.seriesName}: ` : ""}
            {formatValue(hover.point.y)}
          </span>
        </div>
      )}
      <Legend names={data.series.map((series) => series.name)} />
    </div>
  );
}
