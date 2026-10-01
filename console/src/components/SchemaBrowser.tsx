/**
 * The schema, as the agent's introspection reads it.
 *
 * Every table the agent could be given, its size estimate, and -- opened --
 * its columns, types, keys and the comments the agent's prompt is written
 * from. A table whose comment is missing or wrong is one of the commonest
 * reasons a question is answered from the wrong place, and it is invisible
 * anywhere but here.
 *
 * Two actions per table: start a query on it, or show the block of the
 * agent's prompt that describes it, sample rows and all.
 */

import { useMemo, useState } from "react";

import { counted } from "../api/format";
import type { TableModel } from "../api/types";

export interface SchemaBrowserProps {
  tables: readonly TableModel[];
  error: string | null;
  onQuery: (table: string) => void;
  onPrompt: (table: string) => void;
}

function matches(table: TableModel, filter: string): boolean {
  const needle = filter.trim().toLowerCase();
  if (!needle) return true;
  return (
    table.name.toLowerCase().includes(needle) ||
    table.columns.some((column) => column.name.toLowerCase().includes(needle))
  );
}

export function SchemaBrowser({ tables, error, onQuery, onPrompt }: SchemaBrowserProps) {
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const shown = useMemo(() => tables.filter((table) => matches(table, filter)), [tables, filter]);

  return (
    <aside className="schema" aria-label="Schema">
      <h2 className="panel-title">Schema</h2>
      <input
        className="input"
        type="search"
        placeholder="Filter tables and columns"
        aria-label="Filter tables and columns"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
      />
      {error && <p className="error">{error}</p>}
      {!error && shown.length === 0 && (
        <p className="muted small">{tables.length ? "Nothing matches." : "Loading the schema…"}</p>
      )}
      <ul className="schema-list">
        {shown.map((table) => {
          const expanded = open === table.name;
          return (
            <li key={table.name} className="schema-table">
              <button
                type="button"
                className="schema-name"
                aria-expanded={expanded}
                onClick={() => setOpen(expanded ? null : table.name)}
              >
                <span className="mono">{table.name}</span>
                <span className="muted small">~{counted(table.approx_rows, "row")}</span>
              </button>
              {expanded && (
                <div className="schema-detail">
                  <p className={table.comment ? "small" : "small warn"}>
                    {table.comment ?? "No comment: the agent is told nothing about what this table holds."}
                  </p>
                  <div className="schema-actions">
                    <button type="button" className="button button-small" onClick={() => onQuery(table.name)}>
                      Query
                    </button>
                    <button type="button" className="button button-small" onClick={() => onPrompt(table.name)}>
                      Agent's view
                    </button>
                  </div>
                  <ul className="columns">
                    {table.columns.map((column) => (
                      <li key={column.name}>
                        <span className="mono">{column.name}</span>{" "}
                        <span className="muted small">
                          {column.data_type}
                          {column.not_null ? " not null" : ""}
                        </span>
                        {column.comment && <div className="small muted">{column.comment}</div>}
                      </li>
                    ))}
                  </ul>
                  {table.constraints.length > 0 && (
                    <ul className="constraints">
                      {table.constraints.map((constraint) => (
                        <li key={constraint} className="mono small">
                          {constraint}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
