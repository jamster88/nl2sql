/**
 * Writing one snippet: what it means, and the SQL that means it.
 *
 * The form is in the order a curator thinks it: what kind of piece it is,
 * what it is called and what it means -- the half a question is matched
 * against -- then the SQL and the FROM clause it is written over. The two
 * SQL boxes are monospaced and unchecked for spelling, because they are
 * code.
 *
 * Saving is bound to the exact kind and SQL that last validated. The service
 * validates again on every save, so this is a courtesy, not the rule.
 */

import type { ReactNode } from "react";

import { KIND_HINTS } from "../api/snippet";
import { names } from "../api/text";
import type { SchemaModel, SnippetDraftModel, SnippetPreviewModel, SnippetValidationModel } from "../api/types";
import { SnippetCheck } from "./SnippetCheck";

export interface SnippetEditorProps {
  /** The snippet being changed, or null for a new one. */
  snippetId: string | null;
  draft: SnippetDraftModel;
  kinds: readonly string[];
  onChange: (draft: SnippetDraftModel) => void;
  validation: SnippetValidationModel | null;
  stale: boolean;
  preview: SnippetPreviewModel | null;
  schema: SchemaModel | null;
  onValidate: () => void;
  onPreview: () => void;
  onSave: () => void;
  canSave: boolean;
  /** Why saving is not possible yet, for the button's title. */
  saveBlocked: string;
  busy: boolean;
  /** Further actions beside Save: closing the form, removing the snippet. */
  children?: ReactNode;
}

export function SnippetEditor({
  snippetId,
  draft,
  kinds,
  onChange,
  validation,
  stale,
  preview,
  schema,
  onValidate,
  onPreview,
  onSave,
  canSave,
  saveBlocked,
  busy,
  children,
}: SnippetEditorProps) {
  const hint = KIND_HINTS[draft.kind];
  const set = (field: keyof SnippetDraftModel) => (event: { target: { value: string } }) =>
    onChange({ ...draft, [field]: event.target.value });
  const listed = names(draft.tables);
  const addTable = (table: string) => {
    if (!listed.includes(table)) onChange({ ...draft, tables: [...listed, table].join(", ") });
  };

  return (
    <section className="editor" aria-label={snippetId ? `Snippet ${snippetId}` : "New snippet"}>
      <h3>{snippetId ? `Snippet ${snippetId}` : "A new snippet"}</h3>

      <div className="field-row">
        <div className="field">
          <label htmlFor="snippet-kind">Kind</label>
          <select id="snippet-kind" className="input" value={draft.kind} disabled={busy} onChange={set("kind")}>
            {kinds.map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
        </div>
        <div className="field field-grow">
          <label htmlFor="snippet-name">Name</label>
          <input id="snippet-name" className="input" value={draft.name} disabled={busy} onChange={set("name")} />
        </div>
      </div>

      <div className="field">
        <label htmlFor="snippet-means">Means</label>
        <textarea
          id="snippet-means"
          className="input"
          rows={2}
          value={draft.means}
          disabled={busy}
          placeholder="What it is, in plain words, and which questions it is for"
          onChange={set("means")}
        />
      </div>

      <div className="field">
        <label htmlFor="snippet-keywords">Keywords</label>
        <input
          id="snippet-keywords"
          className="input"
          value={draft.keywords}
          disabled={busy}
          placeholder="the phrases a question uses for it, comma-separated"
          onChange={set("keywords")}
        />
        <p className="hint muted">
          Each phrase matches a question that has every one of its words, so prefer two-word phrases to one
          common word.
        </p>
      </div>

      <div className="field">
        <label htmlFor="snippet-applies">Applies to</label>
        <textarea
          id="snippet-applies"
          className="input mono"
          rows={2}
          value={draft.applies_to}
          disabled={busy}
          spellCheck={false}
          placeholder={hint ? hint.applies : ""}
          onChange={set("applies_to")}
        />
        <p className="hint muted">The FROM clause the snippet is written against, with the aliases it uses.</p>
      </div>

      <div className="field">
        <label htmlFor="snippet-sql">SQL</label>
        <textarea
          id="snippet-sql"
          className="input mono"
          rows={4}
          value={draft.sql}
          disabled={busy}
          spellCheck={false}
          placeholder={hint ? hint.example : ""}
          onChange={set("sql")}
        />
        {hint && <p className="hint muted">{hint.sql}, for example {hint.example}</p>}
      </div>

      <div className="field-row">
        <div className="field field-grow">
          <label htmlFor="snippet-tables">Tables</label>
          <input
            id="snippet-tables"
            className="input"
            value={draft.tables}
            disabled={busy}
            placeholder="left empty, it is filled in from the SQL"
            onChange={set("tables")}
          />
        </div>
        <div className="field field-grow">
          <label htmlFor="snippet-note">Note</label>
          <input
            id="snippet-note"
            className="input"
            value={draft.note}
            disabled={busy}
            placeholder="where it came from, or the mistake it prevents"
            onChange={set("note")}
          />
        </div>
      </div>

      {schema && (
        <details className="schema">
          <summary>Tables in the retail database</summary>
          {schema.error ? (
            <p className="muted">{schema.error}</p>
          ) : (
            <ul className="schema-tables">
              {schema.tables.map((table) => (
                <li key={table.name}>
                  <button type="button" className="button button-quiet" disabled={busy} onClick={() => addTable(table.name)}>
                    {table.name}
                  </button>{" "}
                  <span className="muted mono">{table.columns.map((column) => column.name).join(", ")}</span>
                </li>
              ))}
            </ul>
          )}
        </details>
      )}

      <div className="actions">
        <button
          type="button"
          className="button"
          disabled={busy || !draft.applies_to.trim() || !draft.sql.trim()}
          onClick={onValidate}
        >
          Validate against the live database
        </button>
        <button type="button" className="button" disabled={busy} onClick={onPreview}>
          Preview
        </button>
        <button
          type="button"
          className="button button-primary"
          disabled={busy || !canSave}
          title={canSave ? "Write it into the snippet document and load the store" : saveBlocked}
          onClick={onSave}
        >
          {snippetId ? "Save changes" : "Add snippet"}
        </button>
        {children}
      </div>

      {validation && (
        <SnippetCheck
          validation={validation}
          stale={stale}
          listed={listed}
          onUseTables={(tables) => onChange({ ...draft, tables: tables.join(", ") })}
        />
      )}

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
  );
}
