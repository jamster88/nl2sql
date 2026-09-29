/**
 * EXPLAIN's JSON, flattened. The documents here are the shapes Postgres 18
 * writes for plain EXPLAIN and for EXPLAIN ANALYZE against the retail data.
 */

import { describe, expect, it } from "vitest";

import { flattenPlan } from "../src/api/plan";

const JOIN = [
  {
    Plan: {
      "Node Type": "Hash Join",
      "Join Type": "Inner",
      "Startup Cost": 1.9,
      "Total Cost": 25317.4,
      "Plan Rows": 1291781,
      "Hash Cond": "(s.store_key = d.store_key)",
      Plans: [
        { "Node Type": "Seq Scan", "Relation Name": "fact_pos_retail_sales", Alias: "s", "Total Cost": 20096.25 },
        {
          "Node Type": "Hash",
          Plans: [
            {
              "Node Type": "Index Scan",
              "Relation Name": "dim_store",
              Alias: "dim_store",
              "Index Name": "dim_store_pkey",
              "Index Cond": "(store_key = 3)",
            },
          ],
        },
        "not a node",
      ],
    },
  },
];

describe("a plain EXPLAIN", () => {
  const flat = flattenPlan(JOIN);

  it("walks the tree depth first, with each node's depth", () => {
    expect(flat.nodes.map((node) => [node.depth, node.node])).toEqual([
      [0, "Hash Join (Inner)"],
      [1, "Seq Scan"],
      [1, "Hash"],
      [2, "Index Scan"],
    ]);
  });

  it("names what each node reads, with an alias only when it differs", () => {
    expect(flat.nodes.map((node) => node.target)).toEqual([
      "",
      "fact_pos_retail_sales s",
      "",
      "dim_store using dim_store_pkey",
    ]);
  });

  it("keeps the estimates and says nothing it was not told", () => {
    expect(flat.nodes[0]).toMatchObject({ startupCost: 1.9, totalCost: 25317.4, planRows: 1291781 });
    expect(flat.nodes[2]).toMatchObject({ startupCost: null, totalCost: null, planRows: null });
  });

  it("lists the conditions under the node they belong to", () => {
    expect(flat.nodes[0]!.detail).toEqual(["Hash Cond: (s.store_key = d.store_key)"]);
    expect(flat.nodes[3]!.detail).toEqual(["Index Cond: (store_key = 3)"]);
  });

  it("is not analyzed, and has no timings", () => {
    expect(flat).toMatchObject({ analyzed: false, planningTime: null, executionTime: null });
  });
});

describe("EXPLAIN ANALYZE", () => {
  const flat = flattenPlan([
    {
      Plan: {
        "Node Type": "Aggregate",
        Strategy: "Plain",
        "Total Cost": 23325.26,
        "Actual Total Time": 61.2,
        "Actual Rows": 1,
        "Actual Loops": 1,
        "Group Key": ["store_key", "fiscal_year"],
        Plans: [
          { "Node Type": "Function Scan", "Function Name": "generate_series", "Filter": "(x > 1)" },
          { "Node Type": "CTE Scan", "CTE Name": "totals", "Sort Key": ["total DESC"] },
        ],
      },
      "Planning Time": 0.21,
      "Execution Time": 61.4,
    },
  ]);

  it("carries what happened beside what was expected", () => {
    expect(flat.analyzed).toBe(true);
    expect(flat.nodes[0]).toMatchObject({ node: "Aggregate (Plain)", actualTime: 61.2, actualRows: 1, loops: 1 });
    expect(flat).toMatchObject({ planningTime: 0.21, executionTime: 61.4 });
  });

  it("joins a key that is a list", () => {
    expect(flat.nodes[0]!.detail).toEqual(["Group Key: store_key, fiscal_year"]);
    expect(flat.nodes[2]!.detail).toEqual(["Sort Key: total DESC"]);
  });

  it("names functions and CTEs", () => {
    expect(flat.nodes[1]!.target).toBe("generate_series()");
    expect(flat.nodes[2]!.target).toBe("CTE totals");
  });
});

describe("anything that is not a plan", () => {
  it.each([null, undefined, "EXPLAIN text", [], [null], [{}], [{ Plan: [] }], { Plan: "x" }])(
    "flattens %j to nothing",
    (value) => {
      expect(flattenPlan(value)).toEqual({ nodes: [], planningTime: null, executionTime: null, analyzed: false });
    },
  );

  it("accepts a document on its own as well as inside a list", () => {
    expect(flattenPlan({ Plan: { "Node Type": "Result" } }).nodes).toHaveLength(1);
  });

  it("marks a node with no type rather than dropping it", () => {
    expect(flattenPlan([{ Plan: { "Total Cost": 1 } }]).nodes[0]!.node).toBe("?");
  });

  it("ignores a value of a type it does not expect", () => {
    const node = flattenPlan([{ Plan: { "Node Type": 7, "Total Cost": "12", Filter: 3 } }]).nodes[0]!;
    expect(node).toMatchObject({ node: "?", totalCost: null, detail: [] });
  });
});
