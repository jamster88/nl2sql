import { describe, expect, it } from "vitest";

import { chartData, formatValue, toLabel, toNumber } from "../src/charts/values";
import { leadParagraph, plainText } from "../src/api/text";
import { OTHER_LABEL, SERIES_CAP, SERIES_CAP_ALL_PAIRS, seriesVar, withinCap } from "../src/charts/palette";
import type { ChartSpec, ResultTable } from "../src/api/types";
import { makeResult } from "./helpers";

const BAR: ChartSpec = { kind: "bar", x: "department", y: ["net_sales"], series: null };

describe("toNumber", () => {
  it("reads a numeric that crossed JSON as a string", () => {
    expect(toNumber("719279.97")).toBe(719279.97);
    expect(toNumber(" 42 ")).toBe(42);
  });

  it("keeps real numbers as they are", () => {
    expect(toNumber(42)).toBe(42);
    expect(toNumber(-0.5)).toBe(-0.5);
  });

  it("refuses the values Number() would silently turn into zero", () => {
    expect(toNumber(null)).toBeNull();
    expect(toNumber(undefined)).toBeNull();
    expect(toNumber("")).toBeNull();
    expect(toNumber("   ")).toBeNull();
    expect(toNumber([])).toBeNull();
  });

  it("refuses text and non-finite numbers", () => {
    expect(toNumber("Produce")).toBeNull();
    expect(toNumber(Number.NaN)).toBeNull();
    expect(toNumber(Number.POSITIVE_INFINITY)).toBeNull();
  });

  it("reads a boolean as one or zero, which is how Postgres counts them", () => {
    expect(toNumber(true)).toBe(1);
    expect(toNumber(false)).toBe(0);
  });
});

describe("toLabel", () => {
  it("shows a null as a dash rather than an empty gap", () => {
    expect(toLabel(null)).toBe("—");
    expect(toLabel(undefined)).toBe("—");
  });

  it("passes scalars through", () => {
    expect(toLabel("Produce")).toBe("Produce");
    expect(toLabel(7)).toBe("7");
    expect(toLabel(true)).toBe("true");
  });

  it("serialises anything else rather than printing [object Object]", () => {
    expect(toLabel({ a: 1 })).toBe('{"a":1}');
  });
});

describe("formatValue", () => {
  it("keeps thousands readable and millions short", () => {
    expect(formatValue(719279.97)).toBe("719,280");
    expect(formatValue(4_200_000)).toBe("4.2M");
    expect(formatValue(3_100_000_000)).toBe("3.1B");
  });

  it("does not round a small value into a different one", () => {
    expect(formatValue(0.81)).toBe("0.81");
    expect(formatValue(0.0004)).toBe("0.0004");
  });

  it("handles zero and negatives", () => {
    expect(formatValue(0)).toBe("0");
    expect(formatValue(-1500)).toBe("-1,500");
  });
});

describe("chartData", () => {
  it("turns rows into one series per measure", () => {
    const data = chartData(makeResult(), BAR);
    expect(data).not.toBeNull();
    expect(data?.series).toHaveLength(1);
    expect(data?.series[0]?.name).toBe("net_sales");
    expect(data?.categories).toEqual(["Produce", "Dairy & Eggs", "Bakery"]);
    expect(data?.series[0]?.points.map((point) => point.y)).toEqual([719279.97, 512004.1, 318220.55]);
  });

  it("names the y axis only when there is one measure to name", () => {
    expect(chartData(makeResult(), BAR)?.yLabel).toBe("net_sales");
    const two = makeResult({
      columns: ["department", "net_sales", "units"],
      rows: [["Produce", 10, 2]],
    });
    expect(chartData(two, { ...BAR, y: ["net_sales", "units"] })?.yLabel).toBe("");
  });

  it("gives up when the spec names columns the result does not have", () => {
    expect(chartData(makeResult(), { ...BAR, y: ["gone"] })).toBeNull();
  });

  it("gives up on an empty result", () => {
    expect(chartData(makeResult({ rows: [], row_count: 0 }), BAR)).toBeNull();
  });

  it("gives up when every value in the measure is null", () => {
    const blank = makeResult({ rows: [["Produce", null], ["Bakery", null]] });
    expect(chartData(blank, BAR)).toBeNull();
  });

  it("numbers the bands when there is no x column", () => {
    const rows = makeResult({ columns: ["net_sales"], rows: [[10], [20]] });
    const data = chartData(rows, { kind: "bar", x: null, y: ["net_sales"], series: null });
    expect(data?.categories).toEqual(["1", "2"]);
    expect(data?.xLabel).toBe("");
  });

  it("skips a null cell rather than drawing it as zero", () => {
    const rows = makeResult({ rows: [["Produce", 10], ["Bakery", null], ["Deli", 30]] });
    const points = chartData(rows, BAR)?.series[0]?.points ?? [];
    expect(points.map((point) => point.y)).toEqual([10, 30]);
    // The band for the dropped row is kept, so the axis still shows it.
    expect(chartData(rows, BAR)?.categories).toHaveLength(3);
  });

  it("pivots into one series per value of the series column", () => {
    const rows: ResultTable = {
      columns: ["month", "banner", "net_sales"],
      rows: [
        ["Jan", "Metro", 10],
        ["Jan", "Express", 5],
        ["Feb", "Metro", 12],
        ["Feb", "Express", 7],
      ],
      row_count: 4,
      truncated: false,
    };
    const data = chartData(rows, { kind: "line", x: "month", y: ["net_sales"], series: "banner" });
    expect(data?.series.map((series) => series.name)).toEqual(["Metro", "Express"]);
    expect(data?.categories).toEqual(["Jan", "Feb"]);
    expect(data?.series[0]?.points.map((point) => point.y)).toEqual([10, 12]);
  });

  it("skips a null measure when pivoting, rather than drawing the series at zero", () => {
    const rows: ResultTable = {
      columns: ["month", "banner", "net_sales"],
      rows: [
        ["Jan", "Metro", 10],
        ["Jan", "Express", null],
        ["Feb", "Metro", 12],
        ["Feb", "Express", 7],
      ],
      row_count: 4,
      truncated: false,
    };
    const data = chartData(rows, { kind: "line", x: "month", y: ["net_sales"], series: "banner" });

    expect(data?.series.map((series) => series.name)).toEqual(["Metro", "Express"]);
    // Express has one point, at Feb -- not two with a zero in January.
    expect(data?.series[1]?.points.map((point) => [point.x, point.y])).toEqual([[1, 7]]);
  });

  it("folds series past the colour cap into one rather than dropping them", () => {
    const rows: ResultTable = {
      columns: ["month", "store", "sales"],
      rows: Array.from({ length: SERIES_CAP + 4 }, (_, index) => ["Jan", `store-${index}`, index + 1]),
      row_count: SERIES_CAP + 4,
      truncated: false,
    };
    const data = chartData(rows, { kind: "bar", x: "month", y: ["sales"], series: "store" });
    expect(data?.series).toHaveLength(SERIES_CAP);
    expect(data?.series.at(-1)?.name).toBe(OTHER_LABEL);
    // Nothing was lost: the folded series carries every point past the cap.
    expect(data?.series.at(-1)?.points).toHaveLength(5);
  });

  describe("scatter", () => {
    const rows: ResultTable = {
      columns: ["price", "units", "banner"],
      rows: [
        [1.5, 200, "Metro"],
        [2.5, 140, "Metro"],
        [3.5, 90, "Express"],
      ],
      row_count: 3,
      truncated: false,
    };

    it("scales both axes as numbers", () => {
      const data = chartData(rows, { kind: "scatter", x: "price", y: ["units"], series: null });
      expect(data?.numericX).toBe(true);
      expect(data?.categories).toEqual([]);
      expect(data?.series[0]?.points.map((point) => point.x)).toEqual([1.5, 2.5, 3.5]);
      expect(data?.series[0]?.name).toBe("units");
    });

    it("labels a point with both of its coordinates", () => {
      const data = chartData(rows, { kind: "scatter", x: "price", y: ["units"], series: null });
      expect(data?.series[0]?.points[0]?.label).toBe("1.5, 200");
    });

    it("splits into series when a series column is named", () => {
      const data = chartData(rows, { kind: "scatter", x: "price", y: ["units"], series: "banner" });
      expect(data?.series.map((series) => series.name)).toEqual(["Metro", "Express"]);
    });

    it("caps harder than the other forms, because every pair is on screen at once", () => {
      const many: ResultTable = {
        columns: ["price", "units", "banner"],
        rows: Array.from({ length: 6 }, (_, index) => [index, index * 2, `banner-${index}`]),
        row_count: 6,
        truncated: false,
      };
      const data = chartData(many, { kind: "scatter", x: "price", y: ["units"], series: "banner" });
      expect(data?.series).toHaveLength(SERIES_CAP_ALL_PAIRS);
      expect(data?.series.at(-1)?.name).toBe(OTHER_LABEL);
    });

    it("gives up without an x column", () => {
      expect(chartData(rows, { kind: "scatter", x: null, y: ["units"], series: null })).toBeNull();
    });

    it("gives up when the x column named is not in the result", () => {
      expect(chartData(rows, { kind: "scatter", x: "gone", y: ["units"], series: null })).toBeNull();
    });

    it("gives up when no row has both coordinates", () => {
      const sparse: ResultTable = {
        columns: ["price", "units"],
        rows: [[null, 5], [1, null]],
        row_count: 2,
        truncated: false,
      };
      expect(chartData(sparse, { kind: "scatter", x: "price", y: ["units"], series: null })).toBeNull();
    });
  });
});

describe("palette", () => {
  it("assigns slots in order, never a generated colour", () => {
    expect(seriesVar(0)).toBe("var(--series-1)");
    expect(seriesVar(7)).toBe("var(--series-8)");
  });

  it("wraps rather than inventing a ninth slot", () => {
    expect(seriesVar(SERIES_CAP)).toBe("var(--series-1)");
  });

  it("caps at three where every pair is compared at once", () => {
    const names = ["a", "b", "c", "d", "e"];
    expect(withinCap(names)).toHaveLength(5);
    expect(withinCap(names, true)).toHaveLength(3);
  });
});

describe("plainText", () => {
  it("undoes the three entities the pipeline escapes", () => {
    expect(plainText("Thrift &amp; Table")).toBe("Thrift & Table");
    expect(plainText("&lt;none&gt;")).toBe("<none>");
  });

  it("leaves text that was never escaped alone", () => {
    expect(plainText("Produce led with $719,279.97.")).toBe("Produce led with $719,279.97.");
    expect(plainText("")).toBe("");
  });

  it("does not decode an entity the pipeline never writes", () => {
    // A general decoder would turn this into a symbol, which is wrong: it is
    // a literal in a product name, not markup.
    expect(plainText("Acme &copy; Foods")).toBe("Acme &copy; Foods");
  });

  it("gets the order right for text that genuinely contained an entity", () => {
    // A value literally spelled "&lt;" is escaped to "&amp;lt;". Decoding
    // "&lt;" first would find nothing and then produce "<".
    expect(plainText("&amp;lt;")).toBe("&lt;");
  });
});

describe("leadParagraph", () => {
  it("takes the prose and leaves the markdown table behind", () => {
    const document = "Produce led.\n\n| a | b |\n| --- | --- |\n| 1 | 2 |";
    expect(leadParagraph(document)).toBe("Produce led.");
  });

  it("un-escapes as it goes", () => {
    expect(leadParagraph("Thrift &amp; Table led.\n\n| a |")).toBe("Thrift & Table led.");
  });

  it("returns the whole thing when there is no table after it", () => {
    expect(leadParagraph("That is outside what this database covers.")).toBe(
      "That is outside what this database covers.",
    );
  });

  it("is empty for an empty answer", () => {
    expect(leadParagraph("")).toBe("");
  });
});
