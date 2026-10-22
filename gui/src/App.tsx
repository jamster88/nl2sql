/**
 * The application.
 *
 * One question at a time, because that is what the server does: questions
 * queue behind each other on a single Ollama host, so a UI that let four run
 * at once would only be four spinners. What it does keep is everything
 * already answered this session, so comparing two answers is a click.
 *
 * Since 8.0 the questions asked are a conversation, when the server
 * remembers one: the page makes the conversation's id when it opens, sends it
 * with every question, and makes a new one on "New conversation". The session
 * list is not the conversation. It is the jobs answered, and a new
 * conversation leaves it as it was.
 *
 * Every collaborator is a prop with a default. That is not ceremony either:
 * it is what lets the whole interface be tested against a fake client with
 * no server, no container and no network, which is the only way these paths
 * get exercised on every run.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AnswerView } from "./components/AnswerView";
import { AskBox } from "./components/AskBox";
import { History } from "./components/History";
import { Progress } from "./components/Progress";
import { StatusBar } from "./components/StatusBar";
import { createClient, type Client } from "./api/client";
import { newConversationId, remembers } from "./api/conversation";
import { useAsk } from "./api/useAsk";
import type { JobWatchOptions } from "./api/events";
import { createApiFeedbackStore } from "./feedback/apiStore";
import type { FeedbackStore } from "./feedback/store";
import type { Job, Meta } from "./api/types";

export interface AppProps {
  client?: Client;
  store?: FeedbackStore;
  watch?: JobWatchOptions;
}

/** How many answered questions stay in the session list. */
export const HISTORY_LIMIT = 20;

export function App({ client: given, store: givenStore, watch }: AppProps) {
  const client = useMemo(() => given ?? createClient(), [given]);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  // Whether the server takes feedback, in a ref rather than state: the store
  // is built once and reads this on every vote, so it must see the value
  // `/v1/meta` brings back *after* the store was built.
  const accepted = useRef(false);
  // The API-backed store, always. On a server with no staging database it
  // behaves exactly like the browser-only one it wraps -- the verdict is
  // recorded and shown, and nothing claims to have sent it anywhere.
  const store = useMemo(
    () =>
      givenStore ??
      createApiFeedbackStore({
        client,
        enabled: () => accepted.current,
        onError: (error) => setFeedbackError(error.message),
      }),
    [givenStore, client],
  );

  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaError, setMetaError] = useState<Error | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [history, setHistory] = useState<Job[]>([]);
  const [selected, setSelected] = useState<Job | null>(null);
  const [conversation, setConversation] = useState(newConversationId);
  // Said once, after "New conversation", until the next question is asked.
  const [fresh, setFresh] = useState(false);
  const conversing = remembers(meta) > 0;

  const remember = useCallback((job: Job) => {
    setHistory((previous) => [job, ...previous.filter((old) => old.id !== job.id)].slice(0, HISTORY_LIMIT));
  }, []);

  const ask = useAsk(client, { ...(watch ? { watch } : {}), onFinished: remember });

  useEffect(() => {
    const abort = new AbortController();
    client
      .meta(abort.signal)
      .then((value) => {
        accepted.current = value.feedback;
        setMeta(value);
      })
      .catch((error: unknown) => {
        if (abort.signal.aborted) return;
        setMetaError(error instanceof Error ? error : new Error(String(error)));
      });

    // Readiness is asked for separately and its failure is not shown as a
    // connection failure: a server that is up but whose database is still
    // starting should say which, not look unreachable.
    client
      .readiness(abort.signal)
      .then((readiness) => {
        const failed = Object.entries(readiness.checks)
          .filter(([, check]) => !check.ok)
          .map(([name, check]) => `${name}: ${check.detail}`);
        setWarnings([...readiness.warnings, ...failed]);
      })
      .catch(() => undefined);

    return () => {
      abort.abort();
    };
  }, [client]);

  // The answer on screen: whatever the user picked from the session list, or
  // else the one just produced.
  const showing = selected ?? ask.job;
  const live = selected === null;

  const onAsk = useCallback(
    (question: string) => {
      setSelected(null);
      setFresh(false);
      // Only while the server says it remembers: one that keeps nothing
      // would echo the id and change nothing, and one older than 8.0 would
      // refuse a field it does not know.
      ask.ask(question, conversing ? conversation : undefined);
    },
    [ask, conversing, conversation],
  );

  const onNewConversation = useCallback(() => {
    setConversation(newConversationId());
    setFresh(true);
  }, []);

  return (
    <div className="app">
      <header className="masthead">
        <h1>
          NL2SQL <span className="masthead-sub">ask the retail database</span>
        </h1>
      </header>

      <main className="main">
        <AskBox
          onAsk={onAsk}
          onCancel={ask.cancel}
          busy={ask.busy}
          showExamples={history.length === 0 && !ask.busy}
          onNewConversation={conversing ? onNewConversation : undefined}
        />

        {fresh && (
          <p className="notice notice-quiet" role="status">
            A new conversation: the next question is read without the ones before it. They stay in the
            list of earlier questions.
          </p>
        )}

        {ask.error && (
          <div className="notice notice-error" role="alert">
            <strong>That question could not be sent.</strong>
            <p>{ask.error.message}</p>
          </div>
        )}

        {ask.notice && (
          <p className="notice notice-quiet" role="status">
            {ask.notice}
          </p>
        )}

        {live && ask.busy && (
          <Progress
            events={ask.progress}
            nodes={meta?.pipeline.nodes ?? []}
            done={ask.phase === "finished"}
            ensemble={meta?.pipeline.ensemble ?? null}
          />
        )}

        {showing && showing.status !== "queued" && showing.status !== "running" && (
          <AnswerView job={showing} store={store} pipeline={meta?.pipeline ?? null} />
        )}

        <History
          jobs={history}
          currentId={showing?.id}
          onSelect={setSelected}
          store={store}
        />
      </main>

      <StatusBar
        meta={meta}
        error={metaError}
        warnings={feedbackError ? [...warnings, `feedback: ${feedbackError}`] : warnings}
      />
    </div>
  );
}
