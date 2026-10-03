/**
 * A snippet as the form holds it, and what the form says about each kind.
 *
 * The rules here are a convenience -- they grey out a button and say why.
 * The service is what decides: it runs the snippet against the retail
 * database on every save, and parses the document it writes with the
 * loader's own parser.
 */

import type { SnippetDraftModel, SnippetModel } from "./types";

export const EMPTY_SNIPPET: SnippetDraftModel = {
  name: "",
  kind: "filter",
  tables: "",
  keywords: "",
  means: "",
  applies_to: "",
  sql: "",
  note: "",
};

/** What each kind's SQL is, and an example of it, for the form to show. */
export const KIND_HINTS: Record<string, { sql: string; example: string; applies: string }> = {
  join: {
    sql: "A JOIN clause, aliases and all",
    example: "JOIN dim_date d ON d.date_key = f.sales_date_key",
    applies: "fact_pos_retail_sales f",
  },
  filter: {
    sql: "A WHERE condition, without the WHERE",
    example: "p.is_private_label",
    applies: "dim_product p",
  },
  measure: {
    sql: "An aggregate expression: one value over the rows",
    example: "SUM(f.net_sales_amt)",
    applies: "fact_pos_retail_sales f",
  },
  dimension: {
    sql: "An expression to group or label results by",
    example: "'FY' || d.fiscal_year || ' Q' || d.fiscal_quarter",
    applies: "dim_date d",
  },
};

/** A stored snippet, as a draft to edit. */
export function toDraft(snippet: SnippetModel): SnippetDraftModel {
  return {
    name: snippet.name,
    kind: snippet.kind,
    tables: snippet.tables.join(", "),
    keywords: snippet.keywords.join(", "),
    means: snippet.means,
    applies_to: snippet.applies_to,
    sql: snippet.sql,
    note: snippet.note,
  };
}

/** The part of a draft a validation is about: the kind and the two pieces of SQL. */
export function runKey(draft: SnippetDraftModel): string {
  return JSON.stringify([draft.kind, draft.applies_to.trim(), draft.sql.trim()]);
}

/** Fields still empty that a snippet cannot be written without. */
export function missing(draft: SnippetDraftModel): string[] {
  const required: (keyof SnippetDraftModel)[] = ["name", "kind", "keywords", "means", "applies_to", "sql"];
  return required.filter((field) => !draft[field].trim());
}
