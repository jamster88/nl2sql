/**
 * The query, and the three ways to run it.
 *
 * A textarea rather than a code editor: a query pasted from the agent's
 * answer is a few lines, and an editor component would be the largest
 * dependency in the bundle to save nobody any time. Ctrl or Cmd with Enter
 * runs it, because that is what every other SQL tool does.
 *
 * *Plan* stops at EXPLAIN, which is exactly the agent's planner gate and
 * costs nothing. *Analyze* runs the query to time it -- the one to reach
 * for when the agent's answer was a timeout.
 */

import type { KeyboardEvent } from "react";

import type { Mode } from "../api/types";

export interface SqlEditorProps {
  sql: string;
  busy: boolean;
  maxLength: number;
  onChange: (sql: string) => void;
  onRun: (mode: Mode) => void;
}

export function SqlEditor({ sql, busy, maxLength, onChange, onRun }: SqlEditorProps) {
  const empty = !sql.trim();

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      if (!empty && !busy) onRun("run");
    }
  }

  return (
    <section className="editor" aria-label="Query">
      <label className="panel-title" htmlFor="sql">
        SQL
      </label>
      <textarea
        id="sql"
        className="sql"
        spellCheck={false}
        rows={9}
        maxLength={maxLength}
        placeholder="SELECT … -- paste the agent's query, or write your own"
        value={sql}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className="editor-actions">
        <button type="button" className="button button-primary" disabled={empty || busy} onClick={() => onRun("run")}>
          {busy ? "Running…" : "Run"}
        </button>
        <button type="button" className="button" disabled={empty || busy} onClick={() => onRun("plan")}>
          Plan
        </button>
        <button type="button" className="button" disabled={empty || busy} onClick={() => onRun("analyze")}>
          Analyze
        </button>
        <button type="button" className="button button-quiet" disabled={empty || busy} onClick={() => onChange("")}>
          Clear
        </button>
        <span className="muted small">Ctrl/⌘ + Enter runs it · read-only, as the agent's role</span>
      </div>
    </section>
  );
}
