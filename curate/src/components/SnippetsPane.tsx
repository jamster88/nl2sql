/**
 * The SQL snippets: the list beside one snippet being written.
 *
 * The list is the document -- `context_questions/sql_snippets.md` -- read by
 * the service with the loader's parser, filtered here by kind and by words.
 * Beside it is the store the agent reads, and whether it holds this
 * document: a store behind its document is one the agent is not yet being
 * shown the latest of.
 *
 * Writing one is: say what it means, write the SQL, validate it against the
 * live retail database, then save -- which writes the document and loads the
 * store. Removing one does the same in reverse.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import type { Client } from "../api/client";
import { EMPTY_SNIPPET, missing, runKey, toDraft } from "../api/snippet";
import { counted, matches, message } from "../api/text";
import type {
  SchemaModel,
  SnippetDraftModel,
  SnippetModel,
  SnippetPreviewModel,
  SnippetResultModel,
  SnippetSet,
  SnippetValidationModel,
} from "../api/types";
import { Problem } from "./Problem";
import { RemoveButton } from "./RemoveButton";
import { SnippetEditor } from "./SnippetEditor";
import { Steps } from "./Steps";

export interface SnippetsPaneProps {
  client: Client;
  /** Something was written: whatever shows counts should ask again. */
  onChanged: () => void;
}

/** What a write said, in a sentence. */
function said(result: SnippetResultModel): string {
  const verb = { added: "Added", changed: "Saved", removed: "Removed" }[result.action];
  const where = result.action === "removed" ? "from" : "in";
  return `${verb} ${result.snippet_id} ${where} the snippet document — ${counted(result.snippets_after, "snippet")} now.`;
}

export function SnippetsPane({ client, onChanged }: SnippetsPaneProps) {
  const [set, setSet] = useState<SnippetSet | null>(null);
  const [schema, setSchema] = useState<SchemaModel | null>(null);
  const [kind, setKind] = useState("all");
  const [search, setSearch] = useState("");

  const [editing, setEditing] = useState<string | null | undefined>(undefined);
  const [draft, setDraft] = useState<SnippetDraftModel>(EMPTY_SNIPPET);
  const [validation, setValidation] = useState<SnippetValidationModel | null>(null);
  const [validatedFor, setValidatedFor] = useState<string | null>(null);
  const [preview, setPreview] = useState<SnippetPreviewModel | null>(null);
  const [result, setResult] = useState<SnippetResultModel | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    client
      .snippets()
      .then(setSet)
      .catch((cause: unknown) => setError(message(cause)));
  }, [client]);

  useEffect(refresh, [refresh]);

  const startEditing = useCallback(
    (snippetId: string | null, next: SnippetDraftModel) => {
      setEditing(snippetId);
      setDraft(next);
      setValidation(null);
      setValidatedFor(null);
      setPreview(null);
      setError(null);
      if (schema === null) {
        client
          .schema()
          .then(setSchema)
          .catch((cause: unknown) => setSchema({ tables: [], error: message(cause) }));
      }
    },
    [client, schema],
  );

  const all = useMemo(() => set?.snippets ?? [], [set]);
  const shown = useMemo(
    () =>
      all.filter(
        (snippet) =>
          (kind === "all" || snippet.kind === kind) &&
          matches(search, snippet.snippet_id, snippet.name, snippet.means, snippet.keywords.join(" ")),
      ),
    [all, kind, search],
  );

  const run = useCallback(
    <T,>(call: () => Promise<T>, then: (value: T) => void) => {
      setBusy(true);
      setError(null);
      call()
        .then(then)
        .catch((cause: unknown) => setError(message(cause)))
        .finally(() => setBusy(false));
    },
    [],
  );

  const validate = () => {
    const sent = draft;
    run(
      () => client.validateSnippet(sent),
      (value) => {
        setValidation(value);
        setValidatedFor(runKey(sent));
      },
    );
  };

  const showPreview = () => run(() => client.previewSnippet(draft, editing ?? undefined), setPreview);

  const written = (value: SnippetResultModel) => {
    setResult(value);
    setEditing(undefined);
    refresh();
    onChanged();
  };

  const save = () =>
    run(() => (editing ? client.changeSnippet(editing, draft) : client.addSnippet(draft)), written);

  const remove = (snippetId: string) => run(() => client.removeSnippet(snippetId), written);

  const stale = validation !== null && validatedFor !== runKey(draft);
  const blank = missing(draft);
  const canSave = validation?.valid === true && !stale && blank.length === 0;
  const saveBlocked =
    blank.length > 0 ? `Fill in ${blank.join(", ")} first` : "Validate it against the live database first; it has to pass";
  const kinds = set?.kinds ?? [];
  const store = set?.store;

  return (
    <div className="pane">
      <aside className="list" aria-label="Snippets">
        <div className="filters" role="group" aria-label="Filter by kind">
          {["all", ...kinds].map((entry) => (
            <button
              key={entry}
              type="button"
              className="chip"
              aria-pressed={kind === entry}
              onClick={() => setKind(entry)}
            >
              {entry}
              <span className="chip-count">
                {entry === "all" ? all.length : all.filter((s) => s.kind === entry).length}
              </span>
            </button>
          ))}
        </div>
        <input
          className="input"
          type="search"
          aria-label="Search snippets"
          placeholder="search names, meanings, keywords"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <button type="button" className="button button-primary" onClick={() => startEditing(null, EMPTY_SNIPPET)}>
          New snippet
        </button>

        {set?.error && <p className="notice notice-error">{set.error}</p>}
        {store && (
          <p className={`store ${store.current ? "muted" : "store-behind"}`} aria-label="Snippet store">
            {store.reachable ? `Store: ${store.detail}` : `The snippet store cannot be reached: ${store.detail}`}
          </p>
        )}

        <ul className="items">
          {shown.map((snippet: SnippetModel) => (
            <li key={snippet.snippet_id}>
              <button
                type="button"
                className="item"
                aria-current={editing === snippet.snippet_id}
                onClick={() => startEditing(snippet.snippet_id, toDraft(snippet))}
              >
                <span className="item-id">{snippet.snippet_id}</span>
                <span className={`kind kind-${snippet.kind}`}>{snippet.kind}</span>
                <span className="item-title">{snippet.name}</span>
              </button>
            </li>
          ))}
        </ul>
        {set && shown.length === 0 && <p className="muted">No snippet matches.</p>}
      </aside>

      <div className="detail">
        <Problem error={error} />
        {result && (
          <div className={`notice ${result.reloaded ? "notice-ok" : "notice-quiet"}`} role="status">
            <p>
              <strong>{said(result)}</strong>{" "}
              {result.reloaded
                ? result.action === "removed"
                  ? "The store is loaded; the agent is not shown it from the next question."
                  : "The store is loaded; the agent is shown it from the next question."
                : "The document is written; the store catches up at the next load."}
            </p>
            <Steps steps={result.steps} />
            <button type="button" className="button button-quiet" onClick={() => setResult(null)}>
              Dismiss
            </button>
          </div>
        )}

        {editing === undefined ? (
          <p className="muted detail-empty">
            Pick a snippet to change it, or start a new one. A snippet is one piece of SQL -- a join, a filter, a
            measure or a dimension -- beside what it means, and the agent's SQL generator is shown the ones whose
            meaning matches a question.
          </p>
        ) : (
          <SnippetEditor
            snippetId={editing}
            draft={draft}
            kinds={kinds}
            onChange={setDraft}
            validation={validation}
            stale={stale}
            preview={preview}
            schema={schema}
            onValidate={validate}
            onPreview={showPreview}
            onSave={save}
            canSave={canSave}
            saveBlocked={saveBlocked}
            busy={busy}
          >
            {editing && (
              <RemoveButton
                what={editing}
                busy={busy}
                consequence={
                  `Takes ${editing} out of context_questions/sql_snippets.md, keeping the previous version ` +
                  "beside it, and out of the snippet store: the agent is not shown it again."
                }
                onConfirm={() => remove(editing)}
              />
            )}
            <button type="button" className="button button-quiet" onClick={() => setEditing(undefined)}>
              Close
            </button>
          </SnippetEditor>
        )}
      </div>
    </div>
  );
}
