/**
 * An EXPLAIN (FORMAT JSON) plan, flattened into rows a table can show.
 *
 * The planner's estimate is what the agent's planner gate compares against
 * its ceiling, so the tree is worth reading node by node: a sequential scan
 * over the sales fact is usually where a runaway cost comes from, and a
 * nested loop estimated at one row and run a million times is usually where
 * a timeout does. `analyze` adds what actually happened beside each
 * estimate.
 *
 * The document is Postgres's and is read defensively: a key this does not
 * know is ignored, and anything that is not a plan flattens to nothing.
 */

export interface PlanNode {
  depth: number;
  /** The node type, with its join type or strategy when it has one. */
  node: string;
  /** What it reads: a relation and its alias, an index, a CTE, a function. */
  target: string;
  /** Its conditions and keys, one per line. */
  detail: string[];
  startupCost: number | null;
  totalCost: number | null;
  planRows: number | null;
  /** From EXPLAIN ANALYZE only. */
  actualTime: number | null;
  actualRows: number | null;
  loops: number | null;
}

export interface FlatPlan {
  nodes: PlanNode[];
  planningTime: number | null;
  executionTime: number | null;
  /** True when the plan carries what happened, not only what was expected. */
  analyzed: boolean;
}

type Json = Record<string, unknown>;

/** Conditions and keys worth showing under a node, in the order they read. */
const DETAIL_KEYS = [
  "Hash Cond",
  "Merge Cond",
  "Index Cond",
  "Recheck Cond",
  "Join Filter",
  "Filter",
  "Sort Key",
  "Group Key",
] as const;

function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(node: Json, key: string): number | null {
  const value = node[key];
  return typeof value === "number" ? value : null;
}

function text(node: Json, key: string): string {
  const value = node[key];
  if (Array.isArray(value)) return value.map(String).join(", ");
  return typeof value === "string" ? value : "";
}

function label(node: Json): string {
  const kind = text(node, "Node Type") || "?";
  const qualifier = text(node, "Join Type") || text(node, "Strategy");
  return qualifier ? `${kind} (${qualifier})` : kind;
}

function target(node: Json): string {
  const relation = text(node, "Relation Name");
  const alias = text(node, "Alias");
  const parts = [
    relation && alias && alias !== relation ? `${relation} ${alias}` : relation,
    text(node, "Index Name") && `using ${text(node, "Index Name")}`,
    text(node, "CTE Name") && `CTE ${text(node, "CTE Name")}`,
    text(node, "Function Name") && `${text(node, "Function Name")}()`,
  ];
  return parts.filter(Boolean).join(" ");
}

function walk(node: Json, depth: number, into: PlanNode[]): void {
  into.push({
    depth,
    node: label(node),
    target: target(node),
    detail: DETAIL_KEYS.filter((key) => text(node, key)).map((key) => `${key}: ${text(node, key)}`),
    startupCost: num(node, "Startup Cost"),
    totalCost: num(node, "Total Cost"),
    planRows: num(node, "Plan Rows"),
    actualTime: num(node, "Actual Total Time"),
    actualRows: num(node, "Actual Rows"),
    loops: num(node, "Actual Loops"),
  });
  const children = node["Plans"];
  if (Array.isArray(children)) {
    for (const child of children) if (isObject(child)) walk(child, depth + 1, into);
  }
}

export function flattenPlan(plan: unknown): FlatPlan {
  const document = Array.isArray(plan) ? plan[0] : plan;
  const empty: FlatPlan = { nodes: [], planningTime: null, executionTime: null, analyzed: false };
  if (!isObject(document) || !isObject(document["Plan"])) return empty;

  const root = document["Plan"];
  const nodes: PlanNode[] = [];
  walk(root, 0, nodes);
  return {
    nodes,
    planningTime: num(document, "Planning Time"),
    executionTime: num(document, "Execution Time"),
    analyzed: num(root, "Actual Total Time") !== null,
  };
}
