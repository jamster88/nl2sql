/**
 * Corrections and completions: read either store, add a fix by hand, take one out.
 *
 * The review interface fixes answers people marked wrong or incomplete; this
 * writes a fix no submission produced -- a question somebody knows the agent
 * gets wrong, and the query it should write. Only a query that runs against
 * the live retail database is stored, and it has to differ from the one it
 * corrects when that one is given.
 *
 * Taking out a fix the review queue produced puts that submission back in
 * the queue, as reopening it would.
 */

import { useCallback, useEffect, useState } from "react";

import type { Client } from "../api/client";
import { message } from "../api/text";
import type { CuratedFixRequest, CuratedFixResultModel, FixKind, FixList, FixModel, FixRemovalModel, ValidationModel } from "../api/types";
import { Problem } from "./Problem";
import { RemoveButton } from "./RemoveButton";
import { RunResult } from "./RunResult";

export interface FixesPaneProps {
  client: Client;
  reviewer: string;
  onChanged: () => void;
}

export const KINDS: readonly { kind: FixKind; title: string; explain: string }[] = [
  { kind: "corrections", title: "Corrections", explain: "a question the agent answers wrongly, and the query it should write" },
  { kind: "completions", title: "Completions", explain: "a question the agent answers incompletely, and the query that would be complete" },
];

const EMPTY: CuratedFixRequest = { question: "", sql: "", incorrect_sql: "", review_note: "" };

/** What taking a fix out does, said before it is done. */
export function consequence(kind: FixKind, fix: FixModel): string {
  const out = `Deletes ${fix.fix_id} and its vector from the ${kind} store.`;
  return fix.submission_id
    ? `${out} It came from the review queue, so submission ${fix.submission_id} goes back to pending there.`
    : out;
}

export function FixesPane({ client, reviewer, onChanged }: FixesPaneProps) {
  const [kind, setKind] = useState<FixKind>("corrections");
  const [list, setList] = useState<FixList | null>(null);
  const [selected, setSelected] = useState<FixModel | null>(null);
  const [form, setForm] = useState<CuratedFixRequest | null>(null);
  const [validation, setValidation] = useState<ValidationModel | null>(null);
  const [validatedFor, setValidatedFor] = useState<string | null>(null);
  const [added, setAdded] = useState<CuratedFixResultModel | null>(null);
  const [removed, setRemoved] = useState<FixRemovalModel | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    client
      .fixes(kind)
      .then(setList)
      .catch((cause: unknown) => setError(message(cause)));
  }, [client, kind]);

  useEffect(refresh, [refresh]);

  const run = useCallback(<T,>(call: () => Promise<T>, then: (value: T) => void) => {
    setBusy(true);
    setError(null);
    call()
      .then(then)
      .catch((cause: unknown) => setError(message(cause)))
      .finally(() => setBusy(false));
  }, []);

  const choose = (next: FixKind) => {
    setKind(next);
    setSelected(null);
    setForm(null);
    setAdded(null);
    setRemoved(null);
    setError(null);
  };

  const start = () => {
    setSelected(null);
    setForm(EMPTY);
    setValidation(null);
    setValidatedFor(null);
  };

  const changed = () => {
    refresh();
    onChanged();
  };

  const runKey = (value: CuratedFixRequest) => JSON.stringify([value.sql.trim(), value.incorrect_sql.trim()]);
  const words = KINDS.find((entry) => entry.kind === kind)!;

  return (
    <div className="pane">
      <aside className="list" aria-label="Fixes">
        <div className="filters" role="group" aria-label="Store">
          {KINDS.map((entry) => (
            <button key={entry.kind} type="button" className="chip" aria-pressed={kind === entry.kind} onClick={() => choose(entry.kind)}>
              {entry.title}
            </button>
          ))}
        </div>
        <button type="button" className="button button-primary" onClick={start}>
          New {words.title.toLowerCase().replace(/s$/, "")}
        </button>
        {list && <p className="muted">{list.count} in the {kind} store, newest first</p>}
        <ul className="items">
          {(list?.fixes ?? []).map((fix) => (
            <li key={fix.fix_id}>
              <button type="button" className="item" aria-current={selected?.fix_id === fix.fix_id} onClick={() => {
                setSelected(fix);
                setForm(null);
              }}>
                <span className="item-id">{fix.fix_id}</span>
                <span className={`kind kind-${fix.source}`}>{fix.source}</span>
                <span className="item-title">{fix.question}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="detail">
        <Problem error={error} />
        {added && (
          <div className="notice notice-ok" role="status">
            <p>
              <strong>
                Stored {added.fix.fix_id} in the {added.kind} store.
              </strong>{" "}
              {added.embedded ? "Its question is embedded." : `Not embedded yet: ${added.embed_detail}.`}
            </p>
            <button type="button" className="button button-quiet" onClick={() => setAdded(null)}>
              Dismiss
            </button>
          </div>
        )}
        {removed && (
          <div className="notice notice-quiet" role="status">
            <p>
              <strong>
                {removed.found ? `Deleted ${removed.fix_id}` : `${removed.fix_id} was already gone`} from the {removed.kind} store.
              </strong>
              {removed.submission && ` Submission ${removed.submission.id} is back in the review queue, pending.`}
            </p>
            <button type="button" className="button button-quiet" onClick={() => setRemoved(null)}>
              Dismiss
            </button>
          </div>
        )}

        {selected && (
          <section className="record" aria-label={`Fix ${selected.fix_id}`}>
            <h3>
              {selected.fix_id} — {selected.question}
            </h3>
            <p className="muted">
              {selected.submission_id ? `From submission ${selected.submission_id}` : "Written by hand"}
              {selected.reviewer ? ` · ${selected.reviewer}` : ""}
              {selected.embedded ? " · embedded" : " · not embedded yet"}
            </p>
            <p>
              <strong>The query that answers it:</strong>
            </p>
            <pre className="mono">{selected.corrected_sql}</pre>
            {selected.incorrect_sql && (
              <>
                <p>
                  <strong>What the agent wrote:</strong>
                </p>
                <pre className="mono">{selected.incorrect_sql}</pre>
              </>
            )}
            {selected.review_note && <p className="muted">{selected.review_note}</p>}
            <div className="actions">
              <RemoveButton
                what={selected.fix_id}
                consequence={consequence(kind, selected)}
                busy={busy}
                onConfirm={() =>
                  run(
                    () => client.removeFix(kind, selected.fix_id),
                    (value) => {
                      setRemoved(value);
                      setSelected(null);
                      changed();
                    },
                  )
                }
              />
            </div>
          </section>
        )}

        {form && (
          <section className="editor" aria-label={`New ${words.title}`}>
            <h3>
              Into the {kind} store: {words.explain}
            </h3>
            <div className="field">
              <label htmlFor="fix-question">Question</label>
              <input id="fix-question" className="input" value={form.question} disabled={busy}
                onChange={(event) => setForm({ ...form, question: event.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="fix-sql">The query that answers it</label>
              <textarea id="fix-sql" className="input mono" rows={8} spellCheck={false} value={form.sql} disabled={busy}
                onChange={(event) => setForm({ ...form, sql: event.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="fix-incorrect">What the agent wrote, if you have it</label>
              <textarea id="fix-incorrect" className="input mono" rows={4} spellCheck={false} value={form.incorrect_sql} disabled={busy}
                onChange={(event) => setForm({ ...form, incorrect_sql: event.target.value })} />
            </div>
            <div className="field">
              <label htmlFor="fix-note">Note</label>
              <input id="fix-note" className="input" value={form.review_note} disabled={busy}
                onChange={(event) => setForm({ ...form, review_note: event.target.value })} />
            </div>
            <div className="actions">
              <button
                type="button"
                className="button"
                disabled={busy || !form.sql.trim()}
                onClick={() => {
                  const sent = form;
                  run(
                    () => client.validateFix(kind, sent.sql, sent.incorrect_sql),
                    (value) => {
                      setValidation(value);
                      setValidatedFor(runKey(sent));
                    },
                  );
                }}
              >
                Validate against the live database
              </button>
              <button
                type="button"
                className="button button-primary"
                disabled={busy || !form.question.trim() || validation?.valid !== true || validatedFor !== runKey(form)}
                title="Store it; the query has to have passed first"
                onClick={() =>
                  run(
                    () => client.addFix(kind, form, reviewer),
                    (value) => {
                      setAdded(value);
                      setForm(null);
                      changed();
                    },
                  )
                }
              >
                Add to {kind}
              </button>
              <button type="button" className="button button-quiet" onClick={() => setForm(null)}>
                Close
              </button>
            </div>
            {validation && <RunResult validation={validation} stale={validatedFor !== runKey(form)} />}
          </section>
        )}

        {!selected && !form && (
          <p className="muted detail-empty">
            Pick a fix to read it or take it out, or add one by hand. Fixes are kept apart from the golden set: a
            record of a mistake is not a benchmark answer.
          </p>
        )}
      </div>
    </div>
  );
}
