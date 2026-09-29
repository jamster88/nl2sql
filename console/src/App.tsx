/**
 * The SQL console.
 *
 * Three columns: the schema as the agent's introspection reads it, the query
 * and what came of it, and the queries run before. What came of a query is
 * two things, kept apart on purpose -- what the *agent* would have done with
 * it (the verdict: which gate, in whose words, against which limit) and what
 * the *database* returned (the rows, or the plan). A troubleshooting session
 * is mostly moving between the two.
 *
 * Every collaborator is a prop with a default, which is what lets the whole
 * interface be tested against a fake client with no console, no database
 * and no network.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { History } from "./components/History";
import { PlanView } from "./components/PlanView";
import { PromptView } from "./components/PromptView";
import { ResultTable } from "./components/ResultTable";
import { SchemaBrowser } from "./components/SchemaBrowser";
import { SqlEditor } from "./components/SqlEditor";
import { StatusBar } from "./components/StatusBar";
import { Verdict } from "./components/Verdict";
import { createClient, type Client } from "./api/client";
import { identifier } from "./api/format";
import { loadHistory, remember, saveHistory, type HistoryEntry } from "./api/history";
import type { ConsoleMeta, Mode, PromptModel, QueryResult, TableModel } from "./api/types";

export interface AppProps {
  client?: Client;
}

/** What the lower half shows. */
type View = "rows" | "plan" | "prompt";

/** The longest statement the console accepts, until it says otherwise. */
const DEFAULT_MAX_SQL_LENGTH = 20000;

function message(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export function App({ client: given }: AppProps) {
  const client = useMemo(() => given ?? createClient(), [given]);

  const [meta, setMeta] = useState<ConsoleMeta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [tables, setTables] = useState<TableModel[]>([]);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  const [sql, setSql] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [view, setView] = useState<View>("rows");
  const [prompt, setPrompt] = useState<PromptModel | null>(null);
  const [promptError, setPromptError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>(loadHistory);

  // --- what the console is connected to --------------------------------

  useEffect(() => {
    client
      .meta()
      .then(setMeta)
      .catch((cause: unknown) => setMetaError(message(cause)));
    client
      .schema()
      .then((schema) => setTables(schema.tables))
      .catch((cause: unknown) => setSchemaError(message(cause)));
  }, [client]);

  // --- a query -----------------------------------------------------------

  const run = useCallback(
    (mode: Mode) => {
      const statement = sql.trim();
      setBusy(true);
      setRunError(null);
      client
        .query(statement, mode)
        .then((value) => {
          setResult(value);
          setView(mode === "run" ? "rows" : "plan");
          setHistory((old) => {
            const next = remember(old, {
              sql: statement,
              mode,
              at: new Date().toISOString(),
              accepted: value.agent.accepted,
            });
            saveHistory(next);
            return next;
          });
        })
        .catch((cause: unknown) => setRunError(message(cause)))
        .finally(() => setBusy(false));
    },
    [client, sql],
  );

  // --- the schema's two actions ------------------------------------------

  const startQuery = useCallback((table: string) => {
    setSql(`SELECT *\nFROM ${identifier(table)}\nLIMIT 50`);
  }, []);

  const showPrompt = useCallback(
    (table: string) => {
      setPromptError(null);
      client
        .prompt(table)
        .then(setPrompt)
        .catch((cause: unknown) => setPromptError(message(cause)))
        .finally(() => setView("prompt"));
    },
    [client],
  );

  const clearHistory = useCallback(() => {
    setHistory([]);
    saveHistory([]);
  }, []);

  const hasPrompt = prompt !== null || promptError !== null;

  return (
    <div className="app">
      <header className="masthead">
        <h1>
          SQL console <span className="masthead-sub">the retail database, as the agent sees it</span>
        </h1>
      </header>

      <div className="main">
        <SchemaBrowser tables={tables} error={schemaError} onQuery={startQuery} onPrompt={showPrompt} />

        <main className="work">
          <SqlEditor
            sql={sql}
            busy={busy}
            maxLength={meta?.limits.max_sql_length ?? DEFAULT_MAX_SQL_LENGTH}
            onChange={setSql}
            onRun={run}
          />

          {runError && (
            <p className="error" role="alert">
              {runError}
            </p>
          )}

          {result && <Verdict result={result} />}

          {(result || hasPrompt) && (
            <div className="tabs" role="tablist" aria-label="What to show">
              {/* Plan and Analyze return no rows by design, so they get no tab for them. */}
              {result?.mode === "run" && (
                <button type="button" role="tab" aria-selected={view === "rows"} onClick={() => setView("rows")}>
                  Rows
                </button>
              )}
              {result && (
                <button type="button" role="tab" aria-selected={view === "plan"} onClick={() => setView("plan")}>
                  Plan
                </button>
              )}
              {hasPrompt && (
                <button type="button" role="tab" aria-selected={view === "prompt"} onClick={() => setView("prompt")}>
                  Agent's view
                </button>
              )}
            </div>
          )}

          {result?.mode === "run" && view === "rows" && <ResultTable result={result} />}
          {result && view === "plan" && <PlanView plan={result.plan} maxPlanCost={result.max_plan_cost} />}
          {view === "prompt" && promptError && <p className="error">{promptError}</p>}
          {view === "prompt" && prompt && !promptError && <PromptView prompt={prompt} />}
        </main>

        <History entries={history} onPick={(entry) => setSql(entry.sql)} onClear={clearHistory} />
      </div>

      <StatusBar meta={meta} error={metaError} />
    </div>
  );
}
