import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { Chart, ScalarTile } from "../src/charts/Chart";
import { Legend, truncate } from "../src/charts/Frame";
import { barPath } from "../src/charts/Plot";
import type { ChartSpec, ResultTable } from "../src/api/types";
import { makeResult } from "./helpers";

afterEach(cleanup);

const BAR: ChartSpec = { kind: "bar", x: "department", y: ["net_sales"], series: null };

function rows(overrides: Partial<ResultTable>): ResultTable {
  return { columns: [], rows: [], row_count: 0, truncated: false, ...overrides };
}

describe("Chart dispatch", () => {
  it("draws nothing when the pipeline asked for a table", () => {
    const { container } = render(<Chart result={makeResult()} spec={{ ...BAR, kind: "table" }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("draws nothing when the spec cannot be honoured", () => {
    const { container } = render(<Chart result={makeResult()} spec={{ ...BAR, y: ["missing"] }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("draws a bar per row", () => {
    const { container } = render(<Chart result={makeResult()} spec={BAR} />);
    expect(container.querySelectorAll("path.chart-bar")).toHaveLength(3);
  });

  it("names the axes it was given", () => {
    render(<Chart result={makeResult()} spec={BAR} />);
    expect(screen.getByRole("img")).toHaveAttribute("aria-label", "net_sales by department");
  });

  it("still describes itself when the spec names no columns to describe", () => {
    // Two measures share the y axis, so neither may name it; and a spec with
    // no x numbers its bands. The label has to stay a sentence either way.
    const bare = rows({ columns: ["a", "b"], rows: [[1, 2]], row_count: 1 });
    render(<Chart result={bare} spec={{ kind: "bar", x: null, y: ["a", "b"], series: null }} />);
    expect(screen.getByRole("img")).toHaveAttribute("aria-label", "value by row");
  });
});

describe("ScalarTile", () => {
  it("shows one number as a number, not as a bar of one", () => {
    const result = rows({ columns: ["store_count"], rows: [[412]], row_count: 1 });
    render(<Chart result={result} spec={{ kind: "scalar", x: null, y: ["store_count"], series: null }} />);
    expect(screen.getByText("412")).toBeDefined();
    expect(screen.getByText("store_count")).toBeDefined();
  });

  it("formats a large scalar the same way an axis would", () => {
    const result = rows({ columns: ["net_sales"], rows: [["4200000"]], row_count: 1 });
    render(<Chart result={result} spec={{ kind: "scalar", x: null, y: ["net_sales"], series: null }} />);
    expect(screen.getByText("4.2M")).toBeDefined();
  });

  it("falls back to the first column when the spec names none", () => {
    const result = rows({ columns: ["n"], rows: [[9]], row_count: 1 });
    render(<ScalarTile result={result} spec={{ kind: "scalar", x: null, y: [], series: null }} />);
    expect(screen.getByText("9")).toBeDefined();
  });

  it("draws nothing when the one value is not a number", () => {
    const result = rows({ columns: ["name"], rows: [["Produce"]], row_count: 1 });
    const { container } = render(<ScalarTile result={result} spec={{ kind: "scalar", x: null, y: ["name"], series: null }} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("draws nothing with no columns at all", () => {
    const { container } = render(
      <ScalarTile result={rows({})} spec={{ kind: "scalar", x: null, y: [], series: null }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("marks", () => {
  const twoSeries = rows({
    columns: ["month", "metro", "express"],
    rows: [
      ["Jan", 10, 6],
      ["Feb", 14, 9],
      ["Mar", 12, 11],
    ],
    row_count: 3,
  });

  it("labels every bar directly when there is a single series", () => {
    render(<Chart result={makeResult()} spec={BAR} />);
    expect(screen.getByText("719,280")).toBeDefined();
  });

  it("stops labelling bars once there are too many to read", () => {
    const many = rows({
      columns: ["store", "sales"],
      rows: Array.from({ length: 20 }, (_, index) => [`store-${index}`, index + 1]),
      row_count: 20,
    });
    const { container } = render(
      <Chart result={many} spec={{ kind: "bar", x: "store", y: ["sales"], series: null }} />,
    );
    expect(container.querySelectorAll("text.chart-value")).toHaveLength(0);
  });

  it("thins the axis labels rather than letting them collide", () => {
    const many = rows({
      columns: ["store", "sales"],
      rows: Array.from({ length: 60 }, (_, index) => [`store-${index}`, index + 1]),
      row_count: 60,
    });
    const { container } = render(
      <Chart result={many} spec={{ kind: "bar", x: "store", y: ["sales"], series: null }} />,
    );
    expect(container.querySelectorAll("text.chart-tick").length).toBeLessThan(60);
  });

  it("gives a grouped bar one mark per series per band", () => {
    const { container } = render(
      <Chart result={twoSeries} spec={{ kind: "grouped_bar", x: "month", y: ["metro", "express"], series: null }} />,
    );
    expect(container.querySelectorAll("path.chart-bar")).toHaveLength(6);
  });

  it("shows a legend for two series and none for one", () => {
    const { container: two } = render(
      <Chart result={twoSeries} spec={{ kind: "grouped_bar", x: "month", y: ["metro", "express"], series: null }} />,
    );
    expect(two.querySelectorAll(".chart-legend li")).toHaveLength(2);

    cleanup();
    const { container: one } = render(<Chart result={makeResult()} spec={BAR} />);
    expect(one.querySelector(".chart-legend")).toBeNull();
  });

  it("draws a line with a marker on every point", () => {
    const { container } = render(
      <Chart result={twoSeries} spec={{ kind: "line", x: "month", y: ["metro"], series: null }} />,
    );
    expect(container.querySelectorAll("path.chart-line")).toHaveLength(1);
    expect(container.querySelectorAll("circle.chart-dot")).toHaveLength(3);
  });

  it("labels the end of each line when there are few enough to fit", () => {
    const { container } = render(
      <Chart result={twoSeries} spec={{ kind: "line", x: "month", y: ["metro", "express"], series: null }} />,
    );
    // Direct labels, which is a different thing from the legend entries:
    // identity is never carried by colour alone in either direction.
    const drawn = [...container.querySelectorAll("text.chart-value")].map((node) => node.textContent);
    expect(drawn).toEqual(["metro", "express"]);
  });

  it("stops labelling lines directly past four series", () => {
    const five = rows({
      columns: ["month", "a", "b", "c", "d", "e"],
      rows: [["Jan", 1, 2, 3, 4, 5]],
      row_count: 1,
    });
    const { container } = render(
      <Chart result={five} spec={{ kind: "line", x: "month", y: ["a", "b", "c", "d", "e"], series: null }} />,
    );
    expect(container.querySelectorAll("text.chart-value")).toHaveLength(0);
  });

  it("draws a scatter as points on a numeric x axis", () => {
    const points = rows({
      columns: ["price", "units"],
      rows: [
        [1.5, 200],
        [2.5, 140],
      ],
      row_count: 2,
    });
    const { container } = render(
      <Chart result={points} spec={{ kind: "scatter", x: "price", y: ["units"], series: null }} />,
    );
    expect(container.querySelectorAll("circle.chart-point")).toHaveLength(2);
  });

  it("draws a zero line when the data goes below it", () => {
    const negative = rows({
      columns: ["month", "change"],
      rows: [
        ["Jan", -40],
        ["Feb", 90],
      ],
      row_count: 2,
    });
    const { container } = render(
      <Chart result={negative} spec={{ kind: "bar", x: "month", y: ["change"], series: null }} />,
    );
    expect(container.querySelector("line.chart-axis")).not.toBeNull();
  });
});

describe("hover", () => {
  it("shows a tooltip for the mark under the pointer and hides it again", () => {
    const { container } = render(<Chart result={makeResult()} spec={BAR} />);
    const bar = container.querySelector("path.chart-bar");

    fireEvent.mouseEnter(bar as Element);
    expect(screen.getByRole("status").textContent).toContain("Produce");
    expect(screen.getByRole("status").textContent).toContain("719,280");

    fireEvent.mouseLeave(bar as Element);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("names the series in the tooltip only when there is more than one", () => {
    const twoSeries = rows({
      columns: ["month", "metro", "express"],
      rows: [["Jan", 10, 6]],
      row_count: 1,
    });
    const { container } = render(
      <Chart result={twoSeries} spec={{ kind: "grouped_bar", x: "month", y: ["metro", "express"], series: null }} />,
    );
    fireEvent.mouseEnter(container.querySelector("path.chart-bar") as Element);
    expect(screen.getByRole("status").textContent).toContain("metro:");
  });

  it("is reachable from the keyboard", () => {
    const { container } = render(<Chart result={makeResult()} spec={BAR} />);
    const bar = container.querySelector("path.chart-bar") as Element;
    expect(bar).toHaveAttribute("tabindex", "0");

    fireEvent.focus(bar);
    expect(screen.getByRole("status")).toBeDefined();
    fireEvent.blur(bar);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("works on a line's markers", () => {
    const line = rows({ columns: ["month", "sales"], rows: [["Jan", 10], ["Feb", 20]], row_count: 2 });
    const { container } = render(
      <Chart result={line} spec={{ kind: "line", x: "month", y: ["sales"], series: null }} />,
    );
    const dot = container.querySelector("circle.chart-dot") as Element;

    fireEvent.mouseEnter(dot);
    expect(screen.getByRole("status").textContent).toContain("Jan");
    fireEvent.mouseLeave(dot);

    fireEvent.focus(dot);
    expect(screen.getByRole("status")).toBeDefined();
    fireEvent.blur(dot);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("works on a scatter's points", () => {
    const points = rows({ columns: ["price", "units"], rows: [[1.5, 200]], row_count: 1 });
    const { container } = render(
      <Chart result={points} spec={{ kind: "scatter", x: "price", y: ["units"], series: null }} />,
    );
    const point = container.querySelector("circle.chart-point") as Element;

    fireEvent.mouseEnter(point);
    expect(screen.getByRole("status").textContent).toContain("1.5, 200");
    fireEvent.mouseLeave(point);

    fireEvent.focus(point);
    expect(screen.getByRole("status")).toBeDefined();
    fireEvent.blur(point);
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("barPath", () => {
  it("rounds the end away from the baseline and squares the end on it", () => {
    const path = barPath(10, 40, 20, 200);
    expect(path).toMatch(/^M10,200/); // starts on the baseline
    expect(path).toContain("Q10,20 14,20"); // rounds into the top-left
    expect(path).toMatch(/L50,200Z$/); // returns to the baseline square
  });

  it("reflects for a bar below the axis, so the rounding stays at the far end", () => {
    const path = barPath(10, 40, 180, 100);
    expect(path).toContain("Q10,180 14,180");
  });

  it("never rounds more than the bar can carry", () => {
    // A 3px-tall bar with a 4px radius would otherwise invert its own corner.
    expect(barPath(0, 40, 197, 200)).toContain("Q0,197 3,197");
  });
});

describe("Legend", () => {
  it("is absent for one series, because the axis already names it", () => {
    const { container } = render(<Legend names={["net_sales"]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists every series with its swatch", () => {
    render(<Legend names={["metro", "express"]} />);
    expect(screen.getByText("metro")).toBeDefined();
    expect(screen.getByText("express")).toBeDefined();
  });
});

describe("truncate", () => {
  it("leaves a label that fits", () => {
    expect(truncate("Produce")).toBe("Produce");
  });

  it("cuts one that does not, rather than rotating it", () => {
    expect(truncate("Frozen Foods and More")).toBe("Frozen Foods…");
  });
});

describe("layout", () => {
  /** Everything the marks draw, as numbers, in the plot group's own frame. */
  function marks(container: HTMLElement): { x: number; y: number }[] {
    const points: { x: number; y: number }[] = [];
    for (const circle of container.querySelectorAll("circle")) {
      points.push({
        x: Number(circle.getAttribute("cx")),
        y: Number(circle.getAttribute("cy")),
      });
    }
    for (const path of container.querySelectorAll("path")) {
      for (const [, x, y] of (path.getAttribute("d") ?? "").matchAll(/(-?[\d.]+),(-?[\d.]+)/g)) {
        points.push({ x: Number(x), y: Number(y) });
      }
    }
    return points;
  }

  // The frame's own constants. A mark outside this box is drawn over an
  // axis label or off the edge of the figure.
  const PLOT = { width: 720 - 68 - 24, height: 300 - 16 - 44 };

  const cases: [string, ResultTable, ChartSpec][] = [
    ["bar", makeResult(), BAR],
    [
      "grouped bar",
      rows({
        columns: ["month", "metro", "express"],
        rows: [
          ["Jan", 10, 6],
          ["Feb", 14, 9],
        ],
        row_count: 2,
      }),
      { kind: "grouped_bar", x: "month", y: ["metro", "express"], series: null },
    ],
    [
      "line",
      rows({ columns: ["month", "sales"], rows: [["Jan", 10], ["Feb", 20]], row_count: 2 }),
      { kind: "line", x: "month", y: ["sales"], series: null },
    ],
    [
      "scatter",
      rows({ columns: ["price", "units"], rows: [[1.5, 200], [3.5, 90]], row_count: 2 }),
      { kind: "scatter", x: "price", y: ["units"], series: null },
    ],
    [
      "bars that cross zero",
      rows({ columns: ["month", "change"], rows: [["Jan", -40], ["Feb", 90]], row_count: 2 }),
      { kind: "bar", x: "month", y: ["change"], series: null },
    ],
    [
      "a single row",
      rows({ columns: ["month", "sales"], rows: [["Jan", 7]], row_count: 1 }),
      { kind: "bar", x: "month", y: ["sales"], series: null },
    ],
    [
      "sixty rows",
      rows({
        columns: ["store", "sales"],
        rows: Array.from({ length: 60 }, (_, index) => [`store-${index}`, index + 1]),
        row_count: 60,
      }),
      { kind: "bar", x: "store", y: ["sales"], series: null },
    ],
  ];

  it.each(cases)("keeps every %s inside the plot area", (_name, result, spec) => {
    const { container } = render(<Chart result={result} spec={spec} />);
    const drawn = marks(container);

    expect(drawn.length).toBeGreaterThan(0);
    for (const point of drawn) {
      expect(point.x).toBeGreaterThanOrEqual(0);
      expect(point.x).toBeLessThanOrEqual(PLOT.width);
      expect(point.y).toBeGreaterThanOrEqual(0);
      expect(point.y).toBeLessThanOrEqual(PLOT.height);
    }
  });

  it("puts every y tick on the axis it labels", () => {
    const { container } = render(<Chart result={makeResult()} spec={BAR} />);
    const gridlines = [...container.querySelectorAll("line.chart-grid")].map((line) =>
      Number(line.getAttribute("y1")),
    );

    expect(gridlines.length).toBeGreaterThan(1);
    // Descending down the SVG, which is ascending in value, and the lowest
    // tick sits exactly on the baseline rather than near it.
    expect(gridlines).toEqual([...gridlines].sort((a, b) => b - a));
    expect(gridlines[0]).toBe(PLOT.height);
  });

  it("anchors bars on the baseline, so their heights are their values", () => {
    const { container } = render(<Chart result={makeResult()} spec={BAR} />);
    const bars = [...container.querySelectorAll("path.chart-bar")];

    // Produce is 719,279.97 and Bakery is 318,220.55, so the first bar must
    // be more than twice as tall as the third -- which is only true if the
    // axis starts at zero.
    const heights = bars.map((bar) => {
      const ys = [...(bar.getAttribute("d") ?? "").matchAll(/,(-?[\d.]+)/g)].map(([, y]) => Number(y));
      return PLOT.height - Math.min(...ys);
    });
    expect(heights[0] ?? 0).toBeGreaterThan((heights[2] ?? 0) * 2);
  });
});
