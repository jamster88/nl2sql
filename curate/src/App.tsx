/**
 * The curation application: what the agent learns from, written directly.
 *
 * Three tabs, one per thing it keeps:
 *
 * * **SQL snippets** -- joins, filters, measures and dimensions beside what
 *   they mean, which the agent's SQL generator is shown when a question
 *   means them (`context_questions/sql_snippets.md`, and its store);
 * * **Golden pairs** -- whole questions and their answers, the set the agent
 *   is measured against (`context_questions/translated_questions.md`);
 * * **Corrections and completions** -- questions the agent gets wrong or
 *   incomplete, and the query it should write.
 *
 * Everything written here is first run against the live retail database,
 * and run again by the service when it is saved. The review interface is
 * the other way in to the last two: through the queue of what people said.
 *
 * Every collaborator is a prop with a default, which is what lets the whole
 * interface be tested against a fake client with no service.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { createClient, type Client } from "./api/client";
import { message } from "./api/text";
import type { ReviewMeta } from "./api/types";
import { FixesPane } from "./components/FixesPane";
import { GoldenPane } from "./components/GoldenPane";
import { SnippetsPane } from "./components/SnippetsPane";
import { StatusBar } from "./components/StatusBar";

export interface AppProps {
  client?: Client;
}

export type Tab = "snippets" | "golden" | "fixes";

export const TABS: readonly { tab: Tab; title: string }[] = [
  { tab: "snippets", title: "SQL snippets" },
  { tab: "golden", title: "Golden pairs" },
  { tab: "fixes", title: "Corrections & completions" },
];

export function App({ client: given }: AppProps) {
  const client = useMemo(() => given ?? createClient(), [given]);
  const [tab, setTab] = useState<Tab>("snippets");
  const [meta, setMeta] = useState<ReviewMeta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState("");

  const refreshMeta = useCallback(() => {
    client
      .meta()
      .then((value) => {
        setMeta(value);
        setMetaError(null);
      })
      .catch((cause: unknown) => setMetaError(message(cause)));
  }, [client]);

  useEffect(refreshMeta, [refreshMeta]);

  return (
    <div className="app">
      <header className="masthead">
        <h1>
          NL2SQL curation <span className="masthead-sub">what the agent learns from, validated before it is written</span>
        </h1>
        <label className="reviewer">
          Curator
          <input
            className="input"
            type="text"
            value={reviewer}
            placeholder="your name"
            onChange={(event) => setReviewer(event.target.value)}
          />
        </label>
      </header>

      <div className="panes" role="tablist" aria-label="What to curate">
        {TABS.map((entry) => (
          <button
            key={entry.tab}
            type="button"
            role="tab"
            className="pane-tab"
            aria-selected={tab === entry.tab}
            onClick={() => setTab(entry.tab)}
          >
            {entry.title}
          </button>
        ))}
      </div>

      <main className="main" role="tabpanel" aria-label={TABS.find((entry) => entry.tab === tab)?.title}>
        {tab === "snippets" && <SnippetsPane client={client} onChanged={refreshMeta} />}
        {tab === "golden" && <GoldenPane client={client} onChanged={refreshMeta} />}
        {tab === "fixes" && <FixesPane client={client} reviewer={reviewer} onChanged={refreshMeta} />}
      </main>

      <StatusBar meta={meta} error={metaError} />
    </div>
  );
}
