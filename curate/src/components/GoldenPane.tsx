/**
 * The golden question set: read it, add a pair by hand, take one out.
 *
 * The review interface promotes pairs out of feedback; this adds one that
 * no feedback produced -- a question somebody knows the agent should be
 * measured on. It is held to the same document rules (the service previews
 * it with the loader's parser) and, since 5.6, to the rule every golden
 * pair is held to: its SQL is run against the live retail database, and has
 * to return its answer, before it is written.
 *
 * Taking out a pair the review queue produced puts that submission back in
 * the queue, as reopening it in the review interface would; the confirmation
 * says so before it happens.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import type { Client } from "../api/client";
import { matches, message } from "../api/text";
import type {
  DraftModel,
  GoldenPairModel,
  GoldenRemovalModel,
  GoldenResultModel,
  GoldenSet,
  PreviewModel,
  ValidationModel,
} from "../api/types";
import { Problem } from "./Problem";
import { RemoveButton } from "./RemoveButton";
import { RunResult } from "./RunResult";
import { Steps } from "./Steps";

export interface GoldenPaneProps {
  client: Client;
  onChanged: () => void;
}

export const EMPTY_PAIR: DraftModel = {
  title: "",
  question: "",
  tables: "",
  keywords: "",
  reasoning_target: "",
  sql_code: "",
  result: "",
  translation_note: "",
  suite: "",
  extra_meta: {},
};

/** The fields the form shows, in order, with what each is for. */
const FIELDS: readonly { field: Exclude<keyof DraftModel, "extra_meta">; label: string; rows?: number; mono?: boolean; hint?: string }[] = [
  { field: "title", label: "Title", hint: "a short title for the `## Qnn -` heading" },
  { field: "question", label: "Question", hint: "phrased the way a person would ask it" },
  { field: "reasoning_target", label: "Reasoning target", rows: 2, hint: "what the pair tests, and where generated SQL goes wrong" },
  { field: "sql_code", label: "SQL", rows: 8, mono: true },
  { field: "result", label: "Result", hint: "the shape of what comes back: one row, five rows by store..." },
  { field: "tables", label: "Tables", hint: "comma-separated" },
  { field: "keywords", label: "Keywords", hint: "comma-separated, for the keyword search" },
  { field: "suite", label: "Suite", hint: "left empty, the pair joins the last suite in the document" },
  { field: "translation_note", label: "Note" },
];

/** What taking a pair out does, said before it is done. */
export function consequence(pair: GoldenPairModel): string {
  const out =
    `Takes ${pair.pair_id} out of context_questions/translated_questions.md, keeping the previous version ` +
    "beside it, and out of both stores the agent's examples come from.";
  return pair.submission_id
    ? `${out} It came from the review queue, so submission ${pair.submission_id} goes back to pending there.`
    : out;
}

export function GoldenPane({ client, onChanged }: GoldenPaneProps) {
  const [golden, setGolden] = useState<GoldenSet | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<GoldenPairModel | null>(null);
  const [draft, setDraft] = useState<DraftModel | null>(null);
  const [validation, setValidation] = useState<ValidationModel | null>(null);
  const [validatedSql, setValidatedSql] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewModel | null>(null);
  const [added, setAdded] = useState<GoldenResultModel | null>(null);
  const [removed, setRemoved] = useState<GoldenRemovalModel | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    client
      .golden()
      .then(setGolden)
      .catch((cause: unknown) => setError(message(cause)));
  }, [client]);

  useEffect(refresh, [refresh]);

  const shown = useMemo(
    () => (golden?.pairs ?? []).filter((pair) => matches(search, pair.pair_id, pair.title, pair.question)),
    [golden, search],
  );

  const run = useCallback(<T,>(call: () => Promise<T>, then: (value: T) => void) => {
    setBusy(true);
    setError(null);
    call()
      .then(then)
      .catch((cause: unknown) => setError(message(cause)))
      .finally(() => setBusy(false));
  }, []);

  const open = (pair: GoldenPairModel) => {
    setSelected(pair);
    setDraft(null);
    setError(null);
  };

  const start = () => {
    setSelected(null);
    setDraft(EMPTY_PAIR);
    setValidation(null);
    setValidatedSql(null);
    setPreview(null);
    setError(null);
  };

  const changed = () => {
    refresh();
    onChanged();
  };

  return (
    <div className="pane">
      <aside className="list" aria-label="Golden pairs">
        <input
          className="input"
          type="search"
          aria-label="Search golden pairs"
          placeholder="search ids, titles, questions"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <button type="button" className="button button-primary" onClick={start}>
          New pair
        </button>
        {golden?.error && <p className="notice notice-error">{golden.error}</p>}
        {golden && (
          <p className="muted">
            {golden.count} pairs · next {golden.next_pair_id || "—"}
          </p>
        )}
        <ul className="items">
          {shown.map((pair) => (
            <li key={pair.pair_id}>
              <button type="button" className="item" aria-current={selected?.pair_id === pair.pair_id} onClick={() => open(pair)}>
                <span className="item-id">{pair.pair_id}</span>
                <span className="item-title">{pair.title}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <div className="detail">
        <Problem error={error} />
        {added && (
          <div className={`notice ${added.promotion.reloaded ? "notice-ok" : "notice-quiet"}`} role="status">
            <p>
              <strong>
                Added {added.promotion.pair_id} to the golden set — {added.promotion.pairs_after} pairs now.
              </strong>{" "}
              {added.promotion.reloaded
                ? "Both stores are reloaded; the agent can be shown it from the next question."
                : "The document is written; the stores catch up at the next load."}
            </p>
            <Steps steps={added.promotion.steps} />
            <button type="button" className="button button-quiet" onClick={() => setAdded(null)}>
              Dismiss
            </button>
          </div>
        )}
        {removed && (
          <div className="notice notice-quiet" role="status">
            <p>
              <strong>Took {removed.pair_id} out of the golden set.</strong>
              {removed.submission && ` Submission ${removed.submission.id} is back in the review queue, pending.`}
            </p>
            <Steps steps={removed.withdrawal.steps} />
            <button type="button" className="button button-quiet" onClick={() => setRemoved(null)}>
              Dismiss
            </button>
          </div>
        )}

        {selected && (
          <section className="record" aria-label={`Pair ${selected.pair_id}`}>
            <h3>
              {selected.pair_id} — {selected.title}
            </h3>
            <p className="muted">{selected.suite}</p>
            <p>
              <strong>Question:</strong> {selected.question}
            </p>
            <p>
              <strong>Reasoning target:</strong> {selected.reasoning_target}
            </p>
            <pre className="mono">{selected.sql_code}</pre>
            <p>
              <strong>Result:</strong> {selected.result}
            </p>
            <p className="muted">
              {selected.tables.join(", ")} · {selected.keywords.join(", ")}
              {selected.submission_id ? ` · from submission ${selected.submission_id}` : " · written by hand"}
            </p>
            <div className="actions">
              <RemoveButton
                what={selected.pair_id}
                consequence={consequence(selected)}
                busy={busy}
                onConfirm={() =>
                  run(
                    () => client.removeGolden(selected.pair_id),
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

        {draft && (
          <section className="editor" aria-label="New golden pair">
            <h3>A new golden pair</h3>
            <p className="muted">
              Written into the golden set as {golden?.next_pair_id || "the next pair"}: the questions the agent is
              measured against. Its SQL has to run against the live retail database and return its answer.
            </p>
            {FIELDS.map(({ field, label, rows, mono, hint }) => (
              <div className="field" key={field}>
                <label htmlFor={`pair-${field}`}>{label}</label>
                {rows ? (
                  <textarea
                    id={`pair-${field}`}
                    className={`input${mono ? " mono" : ""}`}
                    rows={rows}
                    spellCheck={!mono}
                    value={draft[field]}
                    disabled={busy}
                    onChange={(event) => setDraft({ ...draft, [field]: event.target.value })}
                  />
                ) : (
                  <input
                    id={`pair-${field}`}
                    className="input"
                    value={draft[field]}
                    disabled={busy}
                    onChange={(event) => setDraft({ ...draft, [field]: event.target.value })}
                  />
                )}
                {hint && <p className="hint muted">{hint}</p>}
              </div>
            ))}
            <div className="actions">
              <button
                type="button"
                className="button"
                disabled={busy || !draft.sql_code.trim()}
                onClick={() => {
                  const sent = draft.sql_code;
                  run(
                    () => client.validateGolden(sent),
                    (value) => {
                      setValidation(value);
                      setValidatedSql(sent);
                    },
                  );
                }}
              >
                Validate against the live database
              </button>
              <button type="button" className="button" disabled={busy} onClick={() => run(() => client.previewGolden(draft), setPreview)}>
                Preview
              </button>
              <button
                type="button"
                className="button button-primary"
                disabled={busy || validation?.valid !== true || validatedSql !== draft.sql_code}
                title="Write it into the golden set; the SQL has to have passed first"
                onClick={() =>
                  run(
                    () => client.addGolden(draft),
                    (value) => {
                      setAdded(value);
                      setDraft(null);
                      changed();
                    },
                  )
                }
              >
                Add to golden set
              </button>
              <button type="button" className="button button-quiet" onClick={() => setDraft(null)}>
                Close
              </button>
            </div>
            {validation && <RunResult validation={validation} stale={validatedSql !== draft.sql_code} />}
            {preview && (
              <div className="preview" aria-label="Preview">
                {preview.valid ? (
                  <pre className="mono">{preview.markdown}</pre>
                ) : (
                  <ul className="problems">
                    {preview.problems.map((problem) => (
                      <li key={problem}>{problem}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>
        )}

        {!selected && !draft && (
          <p className="muted detail-empty">
            Pick a pair to read it or take it out, or add a new one. The golden set is what the agent's worked
            examples come from and what it is measured against.
          </p>
        )}
      </div>
    </div>
  );
}
