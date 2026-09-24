/**
 * The review application.
 *
 * One submission at a time, beside the queue it came from. The layout is
 * the argument: the left half is what a user said and cannot be edited, the
 * right half is the golden pair being built out of it, and the fact that
 * they are two panels rather than one form is what stops a curator quietly
 * rewriting the question until it matches the SQL.
 *
 * Three actions, and they are deliberately not equivalent:
 *
 * * **Accept** and **Reject** are judgements. They change a column, they
 *   are reversible, and they save the draft alongside so work in progress
 *   survives clicking away.
 * * **Promote** writes `context_questions/translated_questions.md`. It is
 *   the only thing here with a consequence outside this service's own
 *   database, so it is a separate button, it is disabled until the server
 *   says the draft would parse, and what it did is reported in full rather
 *   than as a tick.
 *
 * Every collaborator is a prop with a default, which is what lets the whole
 * interface be tested against a fake client with no service, no database
 * and no network.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { DraftEditor } from "./components/DraftEditor";
import { Original } from "./components/Original";
import { Promoted } from "./components/Promoted";
import { Queue } from "./components/Queue";
import { StatusBar } from "./components/StatusBar";
import { createClient, type Client } from "./api/client";
import { missing, toDraft } from "./api/draft";
import type {
  DraftModel,
  PreviewModel,
  PromotionModel,
  State,
  SubmissionModel,
} from "./api/types";
import type { ReviewMeta } from "./api/types";

export interface AppProps {
  client?: Client;
  /** How long to wait after a keystroke before asking the server to parse. */
  previewDelayMs?: number;
}

export const DEFAULT_PREVIEW_DELAY_MS = 400;

export function App({ client: given, previewDelayMs = DEFAULT_PREVIEW_DELAY_MS }: AppProps) {
  const client = useMemo(() => given ?? createClient(), [given]);

  const [meta, setMeta] = useState<ReviewMeta | null>(null);
  const [metaError, setMetaError] = useState<Error | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const [state, setState] = useState<State | "all">("pending");
  const [submissions, setSubmissions] = useState<SubmissionModel[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);

  const [selected, setSelected] = useState<SubmissionModel | null>(null);
  const [draft, setDraft] = useState<DraftModel | null>(null);
  const [preview, setPreview] = useState<PreviewModel | null>(null);
  const [suiteInForce, setSuiteInForce] = useState("");

  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [promotion, setPromotion] = useState<PromotionModel | null>(null);

  // --- what the server is ---------------------------------------------

  const refreshMeta = useCallback(() => {
    client
      .meta()
      .then((value) => {
        setMeta(value);
        setWarnings(value.warnings);
        setSuiteInForce((current) => current);
      })
      .catch((cause: unknown) =>
        setMetaError(cause instanceof Error ? cause : new Error(String(cause))),
      );
  }, [client]);

  useEffect(refreshMeta, [refreshMeta]);

  // --- the queue --------------------------------------------------------

  const refresh = useCallback(() => {
    setLoading(true);
    client
      .submissions(state === "all" ? {} : { state })
      .then((list) => {
        setSubmissions(list.submissions);
        setCounts(list.counts);
      })
      .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : String(cause)))
      .finally(() => setLoading(false));
  }, [client, state]);

  useEffect(refresh, [refresh]);

  // --- opening one ------------------------------------------------------

  const open = useCallback(
    (submission: SubmissionModel) => {
      setError(null);
      setPromotion(null);
      setPreview(null);
      setSelected(submission);
      setReviewer(submission.reviewer);
      setNote(submission.review_note);
      // Re-fetched rather than used from the list: the list does not carry a
      // seeded draft, and a form bound to the list's empty one would throw
      // away the seed the moment the user typed.
      client
        .submission(submission.id)
        .then((full) => {
          setSelected(full);
          setDraft(toDraft(full.draft));
        })
        .catch((cause: unknown) =>
          setError(cause instanceof Error ? cause.message : String(cause)),
        );
    },
    [client],
  );

  // --- the preview, which is the only opinion that counts ---------------

  useEffect(() => {
    if (selected === null || draft === null) return undefined;
    // Nothing is sent while a required field is blank: the answer is already
    // known, and asking the server to say so on every keystroke of a form
    // that has barely been started is noise.
    if (missing(draft).length > 0) {
      setPreview(null);
      return undefined;
    }
    const timer = setTimeout(() => {
      client
        .preview(selected.id, draft)
        .then((value) => {
          setPreview(value);
          setSuiteInForce(value.suite_in_force);
        })
        .catch(() => setPreview(null));
    }, previewDelayMs);
    return () => clearTimeout(timer);
  }, [client, selected, draft, previewDelayMs]);

  // --- the three actions ------------------------------------------------

  // These two take the submission and the draft rather than reading them
  // from state, so they need no "if either is null" guard. They are only
  // reachable from the branch below where both are known to exist, and a
  // guard that cannot fire is a line nothing can ever test.
  const judge = useCallback(
    (next: State, submission: SubmissionModel, current: DraftModel) => {
      setBusy(true);
      setError(null);
      client
        .review(submission.id, {
          state: next,
          reviewer,
          review_note: note,
          draft: current,
        })
        .then((updated) => {
          setSelected(updated);
          refresh();
          refreshMeta();
        })
        .catch((cause: unknown) => setError(cause instanceof Error ? cause.message : String(cause)))
        .finally(() => setBusy(false));
    },
    [client, reviewer, note, refresh, refreshMeta],
  );

  const promote = useCallback(
    (submission: SubmissionModel, current: DraftModel) => {
      setBusy(true);
      setError(null);
      client
        .promote(submission.id, current, reviewer)
        .then((result) => {
          setPromotion(result);
          refresh();
          refreshMeta();
          return client.submission(submission.id).then(setSelected);
        })
        .catch((cause: unknown) =>
          setError(cause instanceof Error ? cause.message : String(cause)),
        )
        .finally(() => setBusy(false));
    },
    [client, reviewer, refresh, refreshMeta],
  );

  const terminal = selected?.state === "promoted";
  const promotable = preview?.valid === true && !terminal && selected?.state !== "rejected";

  return (
    <div className="app">
      <header className="masthead">
        <h1>
          NL2SQL review <span className="masthead-sub">feedback into golden questions</span>
        </h1>
        <label className="reviewer">
          Reviewer
          <input
            className="input"
            type="text"
            value={reviewer}
            placeholder="your name"
            onChange={(event) => setReviewer(event.target.value)}
          />
        </label>
      </header>

      <main className="main">
        <Queue
          submissions={submissions}
          counts={counts}
          states={meta?.states ?? []}
          state={state}
          currentId={selected?.id}
          onState={setState}
          onSelect={open}
          busy={loading}
        />

        <div className="detail">
          {error && (
            <div className="notice notice-error" role="alert">
              <strong>That did not work.</strong>
              <p>{error}</p>
            </div>
          )}

          {promotion && <Promoted promotion={promotion} onDismiss={() => setPromotion(null)} />}

          {selected === null || draft === null ? (
            <p className="muted detail-empty">
              Pick something from the queue. What a user said is shown on the left of it; the
              golden pair you would build from it goes on the right.
            </p>
          ) : (
            <>
              <Original submission={selected} />

              {terminal ? (
                <p className="notice notice-quiet">
                  Already promoted as <strong>{selected.promoted_pair_id}</strong>. This record is
                  history now and is not editable.
                </p>
              ) : (
                <>
                  <DraftEditor
                    draft={draft}
                    onChange={setDraft}
                    preview={preview}
                    suiteInForce={suiteInForce}
                    disabled={busy}
                  />

                  <div className="field">
                    <label htmlFor="review-note">Review note</label>
                    <textarea
                      id="review-note"
                      className="input"
                      rows={2}
                      value={note}
                      disabled={busy}
                      onChange={(event) => setNote(event.target.value)}
                    />
                  </div>

                  <div className="actions">
                    <button
                      type="button"
                      className="button"
                      disabled={busy}
                      onClick={() => judge("accepted", selected, draft)}
                    >
                      Accept
                    </button>
                    <button
                      type="button"
                      className="button"
                      disabled={busy}
                      onClick={() => judge("rejected", selected, draft)}
                    >
                      Reject
                    </button>
                    <button
                      type="button"
                      className="button button-primary"
                      disabled={busy || !promotable}
                      title={
                        promotable
                          ? "Write this pair into the golden question set"
                          : "Fill in every required field; the preview has to parse first"
                      }
                      onClick={() => promote(selected, draft)}
                    >
                      Promote to golden set
                    </button>
                    {selected.state === "rejected" && (
                      <span className="muted">Rejected — accept it before promoting it.</span>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </main>

      <StatusBar meta={meta} error={metaError} warnings={warnings} />
    </div>
  );
}
