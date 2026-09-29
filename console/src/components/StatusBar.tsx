/**
 * What the console is connected to, and the limits every query runs under.
 *
 * The limits are the agent's, and they are on screen because they are half
 * of every verdict: "refused at the planner" means nothing without the
 * ceiling it was refused against. The role is here because it is the other
 * half -- a console connected as anything but the agent's read-only role is
 * not showing what the agent sees, and says so in the warnings.
 */

import { counted, formatNumber } from "../api/format";
import type { ConsoleMeta } from "../api/types";

export interface StatusBarProps {
  meta: ConsoleMeta | null;
  error: string | null;
}

export function StatusBar({ meta, error }: StatusBarProps) {
  return (
    <footer className="statusbar">
      {error && <span className="status status-bad">Cannot reach the console: {error}</span>}

      {meta && (
        <>
          <span className="status">
            {meta.service} {meta.version}
          </span>
          <span className="status muted">
            {meta.database} as <strong className={meta.read_only ? "good" : "bad"}>{meta.role}</strong>{" "}
            {meta.read_only ? "(read-only)" : "(can write)"} · {meta.db_schema}, {counted(meta.tables, "table")} ·
            Postgres {meta.server_version.split(" ")[0]}
          </span>
          <span className="status muted">
            agent limits: {formatNumber(meta.limits.statement_timeout_ms, 0)} ms ·{" "}
            {meta.limits.agent_max_rows} rows · plan cost {formatNumber(meta.limits.max_plan_cost, 0)}
          </span>
          <span className="status muted">
            shows {meta.limits.max_rows} rows · auth {meta.authentication}
          </span>
          {meta.warnings.map((warning) => (
            <span className="status status-warn" key={warning}>
              {warning}
            </span>
          ))}
        </>
      )}
    </footer>
  );
}
