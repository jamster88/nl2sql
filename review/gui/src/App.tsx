/**
 * The review application.
 *
 * Three panes, one per verdict a user can give, because each verdict has
 * one way forward and they must not be confused:
 *
 * * **Correct** answers are built into golden pairs and written into
 *   `context_questions/translated_questions.md` -- the set the agent is
 *   measured against.
 * * **Wrong** answers are fixed: the reviewer writes the SQL that should
 *   have been generated, runs it against the live retail database, and only
 *   a query that runs goes into the *corrections* store.
 * * **Correct but incomplete** answers are fixed the same way into the
 *   *completions* store -- its own pane, because a missing label and a wrong
 *   join are different lessons, and a later version will treat them
 *   differently.
 *
 * Within a pane, one submission at a time beside its queue. The left half is
 * what a user said and cannot be edited; the right half is what is built out
 * of it. That they are two panels rather than one form is what stops a
 * curator quietly rewriting the question until it matches the SQL.
 *
 * Every collaborator is a prop with a default, which is what lets the whole
 * interface be tested against a fake client with no service, no database
 * and no network.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { DraftEditor } from "./components/DraftEditor";
import { FixEditor } from "./components/FixEditor";
import { Fixed } from "./components/Fixed";
import { Original } from "./components/Original";
import { Promoted } from "./components/Promoted";
import { Queue } from "./components/Queue";
import { StatusBar } from "./components/StatusBar";
import { createClient, type Client } from "./api/client";
import { missing, toDraft } from "./api/draft";
import type {
  DraftModel,
  FixKind,
  FixResultModel,
  PreviewModel,
  PromotionModel,
  State,
  SubmissionModel,
  ValidationModel,
  Verdict,
} from "./api/types";
import type { ReviewMeta } from "./api/types";

export interface AppProps {
  client?: Client;
  /** How long to wait after a keystroke before asking the server to parse. */
  previewDelayMs?: number;
}

export const DEFAULT_PREVIEW_DELAY_MS = 400;

/** The panes, in the order a reviewer meets them. */
export const PANES: readonly { verdict: Verdict; title: string; goesTo: string }[] = [
  { verdict: "yes", title: "Correct", goesTo: "golden set" },
  { verdict: "no", title: "Wrong", goesTo: "corrections" },
  { verdict: "incomplete", title: "Correct but incomplete", goesTo: "completions" },
];

/** Where a fixable verdict's answers are fixed into. */
const FIX_KIND: Record<Exclude<Verdict, "yes">, FixKind> = {
  no: "corrections",
  incomplete: "completions",
};

function message(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

export function App({ client: given, previewDelayMs = DEFAULT_PREVIEW_DELAY_MS }: AppProps) {
  const client = useMemo(() => given ?? createClient(), [given]);

  const [meta, setMeta] = useState<ReviewMeta | null>(null);
  const [metaError, setMetaError] = useState<Error | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  const [pane, setPane] = useState<Verdict>("yes");
  const [state, setState] = useState<State | "all">("pending");
  const [submissions, setSubmissions] = useState<SubmissionModel[]>([]);
  const [countsByVerdict, setCountsByVerdict] = useState<Record<string, Record<string, number>>>({});
  const [loading, setLoading] = useState(false);

  const [selected, setSelected] = useState<SubmissionModel | null>(null);
  const [draft, setDraft] = useState<DraftModel | null>(null);
  const [preview, setPreview] = useState<PreviewModel | null>(null);
  const [suiteInForce, setSuiteInForce] = useState("");

  const [fixSql, setFixSql] = useState("");
  const [validation, setValidation] = useState<ValidationModel | null>(null);
  const [validatedSql, setValidatedSql] = useState<string | null>(null);
  const [fixResult, setFixResult] = useState<FixResultModel | null>(null);

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
      })
      .catch((cause: unknown) =>
        setMetaError(cause instanceof Error ? cause : new Error(String(cause))),
      );
  }, [client]);

  useEffect(refreshMeta, [refreshMeta]);

  // --- the queue for this pane ----------------------------------------

  const refresh = useCallback(() => {
    setLoading(true);
    client
      .submissions(state === "all" ? { verdict: pane } : { state, verdict: pane })
      .then((list) => {
        setSubmissions(list.submissions);
        setCountsByVerdict(list.counts_by_verdict);
      })
      .catch((cause: unknown) => setError(message(cause)))
      .finally(() => setLoading(false));
  }, [client, state, pane]);

  useEffect(refresh, [refresh]);

  // --- switching pane ----------------------------------------------------

  const choosePane = useCallback((next: Verdict) => {
    // A new pane is a new job: nothing opened in the last one stays open.
    setPane(next);
    setSelected(null);
    setDraft(null);
    setPreview(null);
    setValidation(null);
    setValidatedSql(null);
    setFixResult(null);
    setPromotion(null);
    setError(null);
  }, []);

  // --- opening one ------------------------------------------------------

  const open = useCallback(
    (submission: SubmissionModel) => {
      setError(null);
      setPromotion(null);
      setFixResult(null);
      setPreview(null);
      setValidation(null);
      setValidatedSql(null);
      setSelected(submission);
      setReviewer((current) => submission.reviewer || current);
      setNote(submission.review_note);
      // Re-fetched rather than used from the list: the list does not carry a
      // seeded draft, and a form bound to the list's empty one would throw
      // away the seed the moment the user typed.
      client
        .submission(submission.id)
        .then((full) => {
          setSelected(full);
          setDraft(toDraft(full.draft));
          // A fix starts from what the agent wrote: most are an edit.
          setFixSql(full.sql_code);
        })
        .catch((cause: unknown) => setError(message(cause)));
    },
    [client],
  );

  // --- the preview, which is the only opinion that counts ---------------

  useEffect(() => {
    if (selected === null || draft === null || selected.verdict !== "yes") return undefined;
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

  // --- the actions --------------------------------------------------------

  // These take the submission (and draft) rather than reading them from
  // state, so they need no "if either is null" guard. They are only
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
          ...(submission.verdict === "yes" ? { draft: current } : {}),
        })
        .then((updated) => {
          setSelected(updated);
          refresh();
          refreshMeta();
        })
        .catch((cause: unknown) => setError(message(cause)))
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
        .catch((cause: unknown) => setError(message(cause)))
        .finally(() => setBusy(false));
    },
    [client, reviewer, refresh, refreshMeta],
  );

  const validate = useCallback(
    (submission: SubmissionModel) => {
      setBusy(true);
      setError(null);
      const sent = fixSql;
      client
        .validate(submission.id, sent)
        .then((value) => {
          setValidation(value);
          setValidatedSql(sent);
        })
        .catch((cause: unknown) => setError(message(cause)))
        .finally(() => setBusy(false));
    },
    [client, fixSql],
  );

  const saveFix = useCallback(
    (submission: SubmissionModel) => {
      setBusy(true);
      setError(null);
      client
        .fix(submission.id, fixSql, reviewer, note)
        .then((result) => {
          setFixResult(result);
          setSelected(result.submission);
          refresh();
          refreshMeta();
        })
        .catch((cause: unknown) => setError(message(cause)))
        .finally(() => setBusy(false));
    },
    [client, fixSql, reviewer, note, refresh, refreshMeta],
  );

  const promotedAlready = selected?.state === "promoted";
  const fixedAlready = selected?.state === "corrected";
  const terminal = promotedAlready || fixedAlready;
  const rejected = selected?.state === "rejected";
  const promotable = preview?.valid === true && !terminal && !rejected;
  const stale = validation !== null && validatedSql !== fixSql;
  const saveable = validation?.valid === true && !stale && !terminal && !rejected;
  const pendingIn = (verdict: Verdict) => countsByVerdict[verdict]?.pending ?? 0;

  return (
    <div className="app">
      <header className="masthead">
        <h1>
          NL2SQL review <span className="masthead-sub">feedback into golden questions and fixes</span>
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

      <div className="panes" role="tablist" aria-label="What the user said">
        {PANES.map((entry) => (
          <button
            key={entry.verdict}
            type="button"
            role="tab"
            className={`pane-tab pane-${entry.verdict}`}
            aria-selected={pane === entry.verdict}
            onClick={() => choosePane(entry.verdict)}
          >
            {entry.title}
            <span className="pane-goes muted"> → {entry.goesTo}</span>
            <span className="chip-count">{pendingIn(entry.verdict)}</span>
          </button>
        ))}
      </div>

      <main className="main" role="tabpanel" aria-label={PANES.find((p) => p.verdict === pane)?.title}>
        <Queue
          submissions={submissions}
          counts={countsByVerdict[pane] ?? {}}
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
          {fixResult && <Fixed result={fixResult} onDismiss={() => setFixResult(null)} />}

          {selected === null || draft === null ? (
            <p className="muted detail-empty">
              Pick something from the queue. What a user said is shown on the left of it; what
              you build from it -- a golden pair, or a fix -- goes on the right.
            </p>
          ) : (
            <>
              <Original submission={selected} />

              {promotedAlready && (
                <p className="notice notice-quiet">
                  Already promoted as <strong>{selected.promoted_pair_id}</strong>. This record is
                  history now and is not editable.
                </p>
              )}
              {fixedAlready && (
                <p className="notice notice-quiet">
                  Already fixed as <strong>{selected.promoted_pair_id}</strong>. This record is
                  history now and is not editable.
                </p>
              )}

              {!terminal && (
                <>
                  {selected.verdict === "yes" ? (
                    <DraftEditor
                      draft={draft}
                      onChange={setDraft}
                      preview={preview}
                      suiteInForce={suiteInForce}
                      disabled={busy}
                    />
                  ) : (
                    <FixEditor
                      kind={FIX_KIND[selected.verdict]}
                      sql={fixSql}
                      onChange={setFixSql}
                      validation={validation}
                      stale={stale}
                      onValidate={() => validate(selected)}
                      onSave={() => saveFix(selected)}
                      canSave={saveable}
                      busy={busy}
                    />
                  )}

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
                    {selected.verdict === "yes" && (
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
                    )}
                    {rejected && (
                      <span className="muted">
                        Rejected — accept it before{" "}
                        {selected.verdict === "yes" ? "promoting" : "fixing"} it.
                      </span>
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
