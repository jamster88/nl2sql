/**
 * Building the golden pair.
 *
 * Seven fields, and three of them are the job. `title`, `question`,
 * `tables` and `sql_code` arrive from the submission and usually need only
 * a tidy; `keywords`, `reasoning_target` and `result` arrive empty and
 * cannot arrive any other way, because they are judgements about what the
 * question *tests* and no thumbs-up carries them. They are marked, so a
 * reviewer can see at a glance what is actually being asked of them.
 *
 * Nothing here is pre-filled with a guess at those three. A plausible
 * autofill is worse than an empty box: a reviewer skimming a full form
 * approves it, and the BM25 index fills up with keywords nobody chose.
 */

import { JUDGEMENT, REQUIRED } from "../api/draft";
import type { DraftModel, PreviewModel } from "../api/types";

export interface DraftEditorProps {
  draft: DraftModel;
  onChange: (draft: DraftModel) => void;
  preview: PreviewModel | null;
  suiteInForce: string;
  disabled?: boolean;
}

interface FieldSpec {
  name: keyof DraftModel;
  label: string;
  hint: string;
  rows?: number;
}

const FIELDS: FieldSpec[] = [
  { name: "title", label: "Title", hint: "The `## Qnn -` heading. One line." },
  { name: "question", label: "Question", hint: "One line, and it cannot end in a double quote." },
  { name: "tables", label: "Tables", hint: "Comma-separated, as the SQL reads them." },
  {
    name: "keywords",
    label: "Keywords",
    hint: "Comma-separated. This is the BM25 index — the words someone would search for.",
  },
  {
    name: "reasoning_target",
    label: "Reasoning target",
    hint: "One line: what this pair tests, and where generated SQL typically goes wrong.",
    rows: 3,
  },
  { name: "sql_code", label: "SQL", hint: "Verified against the live database. No ``` fences.", rows: 10 },
  { name: "result", label: "Result", hint: "One line: the shape of what comes back." },
  {
    name: "translation_note",
    label: "Translation note",
    hint: "Optional. Left blank, it records that this came from GUI feedback.",
    rows: 2,
  },
];

export function DraftEditor({
  draft,
  onChange,
  preview,
  suiteInForce,
  disabled = false,
}: DraftEditorProps) {
  function set(name: keyof DraftModel, value: string) {
    onChange({ ...draft, [name]: value });
  }

  return (
    <section className="draft" aria-label="Golden pair draft">
      {FIELDS.map((field) => {
        const value = String(draft[field.name] ?? "");
        const required = REQUIRED.includes(field.name);
        const judgement = JUDGEMENT.includes(field.name);
        return (
          <div className="field" key={field.name}>
            <label htmlFor={`draft-${field.name}`}>
              {field.label}
              {required && <span className="required" aria-hidden="true"> *</span>}
              {judgement && <span className="badge-judgement">yours to write</span>}
            </label>
            <p className="field-hint muted">{field.hint}</p>
            {field.rows ? (
              <textarea
                id={`draft-${field.name}`}
                className="input mono"
                rows={field.rows}
                value={value}
                disabled={disabled}
                onChange={(event) => set(field.name, event.target.value)}
              />
            ) : (
              <input
                id={`draft-${field.name}`}
                className="input"
                type="text"
                value={value}
                disabled={disabled}
                onChange={(event) => set(field.name, event.target.value)}
              />
            )}
          </div>
        );
      })}

      <div className="field">
        <label htmlFor="draft-suite">Suite</label>
        <p className="field-hint muted">
          {suiteInForce
            ? `Leave blank to join "${suiteInForce}", the suite a pair appended now inherits.`
            : "Leave blank to inherit whatever suite the document ends in."}
        </p>
        <input
          id="draft-suite"
          className="input"
          type="text"
          value={draft.suite}
          disabled={disabled}
          placeholder={suiteInForce}
          onChange={(event) => set("suite", event.target.value)}
        />
      </div>

      {preview && preview.problems.length > 0 && (
        <div className="notice notice-error" role="alert">
          <strong>This cannot become a golden pair yet.</strong>
          <ul>
            {preview.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      )}

      {preview && preview.valid && (
        <div className="preview">
          <h4>
            Preview <span className="muted">— {preview.pair_id}, as it will be written</span>
          </h4>
          <pre className="code">
            <code>{preview.markdown}</code>
          </pre>
        </div>
      )}
    </section>
  );
}
