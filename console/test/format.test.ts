/**
 * Cells, counts, numbers, identifiers and CSV -- the text a person reads a
 * result from, where NULL and the empty string must never look alike.
 */

import { describe, expect, it } from "vitest";

import { cellText, counted, formatNumber, formatShare, identifier, isNumericType, toCsv } from "../src/api/format";

describe("cellText", () => {
  it("says NULL for a missing value, and keeps an empty string empty", () => {
    expect(cellText(null)).toBe("NULL");
    expect(cellText(undefined)).toBe("NULL");
    expect(cellText("")).toBe("");
  });

  it("shows numbers, booleans and strings as themselves", () => {
    expect(cellText(42)).toBe("42");
    expect(cellText(false)).toBe("false");
    expect(cellText("719279.97")).toBe("719279.97");
  });

  it("shows arrays and JSON as JSON", () => {
    expect(cellText([1, 2])).toBe("[1,2]");
    expect(cellText({ a: 1 })).toBe('{"a":1}');
  });
});

describe("isNumericType", () => {
  it.each(["integer", "bigint", "smallint", "numeric", "numeric(12,2)", "real", "double precision", "money"])(
    "right-aligns %s",
    (type) => expect(isNumericType(type)).toBe(true),
  );

  // An array of numbers is shown as JSON, which does not line up by digit.
  it.each(["text", "character varying", "date", "integer[]", "interval", "bigint_ish"])("leaves %s alone", (type) => {
    expect(isNumericType(type)).toBe(false);
  });
});

describe("counted", () => {
  it("agrees with its number and groups thousands", () => {
    expect(counted(1, "row")).toBe("1 row");
    expect(counted(0, "row")).toBe("0 rows");
    expect(counted(1291781, "row")).toBe("1,291,781 rows");
    expect(counted(2, "query", "queries")).toBe("2 queries");
  });
});

describe("formatNumber", () => {
  it("groups and rounds the way EXPLAIN reads", () => {
    expect(formatNumber(20096.2549)).toBe("20,096.25");
    expect(formatNumber(1000000, 0)).toBe("1,000,000");
    expect(formatNumber(3)).toBe("3");
  });
});

describe("formatShare", () => {
  it("shows a cost against its ceiling as a percentage", () => {
    expect(formatShare(25317.4, 1000000)).toBe("2.5%");
    expect(formatShare(4200000, 1000000)).toBe("420%");
    expect(formatShare(0, 1000000)).toBe("0%");
  });

  it("never rounds a real share down to nothing", () => {
    expect(formatShare(3.82, 1000000)).toBe("<0.1%");
  });
});

describe("identifier", () => {
  it("leaves an ordinary name bare", () => {
    expect(identifier("dim_store")).toBe("dim_store");
  });

  it("quotes one Postgres would fold or refuse, doubling its quotes", () => {
    expect(identifier("Dim Store")).toBe('"Dim Store"');
    expect(identifier('odd"name')).toBe('"odd""name"');
    expect(identifier("2020_sales")).toBe('"2020_sales"');
  });
});

describe("toCsv", () => {
  it("writes a header and one line per row, NULL as an empty field", () => {
    expect(toCsv(["a", "b"], [[1, null], ["x", true]])).toBe("a,b\n1,\nx,true");
  });

  it("quotes a field that holds a comma, a quote or a line break", () => {
    expect(toCsv(["name, full"], [['say "hi"'], ["two\nlines"], ["cr\rhere"]])).toBe(
      '"name, full"\n"say ""hi"""\n"two\nlines"\n"cr\rhere"',
    );
  });

  it("writes JSON cells as JSON", () => {
    expect(toCsv(["j"], [[{ a: 1 }]])).toBe('j\n"{""a"":1}"');
  });
});
