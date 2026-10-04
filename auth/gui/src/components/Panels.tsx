/**
 * The two smaller panels and the status bar: loading people from a file,
 * the role sync that makes them roles in the retail database, and where
 * this page is connected.
 */

import { useState, type ChangeEvent, type FormEvent } from "react";

import type { DirectoryMeta, ImportResult, SyncReport } from "../api/types";

function words(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export interface ImportPanelProps {
  onImport: (filename: string, content: string) => Promise<ImportResult>;
}

/** A CSV or LDIF file, read in the browser and sent as text. */
export function ImportPanel({ onImport }: ImportPanelProps) {
  const [filename, setFilename] = useState("people.csv");
  const [content, setContent] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const read = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setFilename(file.name);
    file.text().then(setContent, (error: unknown) => setProblem(words(error)));
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setProblem(null);
    setResult(null);
    onImport(filename, content).then(setResult, (error: unknown) => setProblem(words(error)));
  };

  return (
    <form className="panel" onSubmit={submit} aria-label="Load people from a file">
      <h2>Load people from a file</h2>
      <p className="hint">
        CSV with a header row (<code>uid,given_name,surname,mail,groups,password</code>), or LDIF exported from another
        directory. New people are added, people already here are updated, and nobody is taken out of a group.
      </p>
      <label>
        File
        <input type="file" accept=".csv,.ldif,.ldf" onChange={read} />
      </label>
      <label>
        Or paste it, named
        <input value={filename} onChange={(event) => setFilename(event.target.value)} aria-label="File name" />
      </label>
      <textarea
        aria-label="File contents"
        rows={6}
        value={content}
        onChange={(event) => setContent(event.target.value)}
      />
      <button type="submit" disabled={!content.trim()}>
        Load
      </button>
      {problem && (
        <p className="notice notice-error" role="alert">
          {problem}
        </p>
      )}
      {result && (
        <div className="notice notice-quiet" role="status">
          <p>
            {result.created.length} added, {result.updated.length} updated, {result.passwords.length} passwords set,{" "}
            {result.groups.length} memberships added.
          </p>
          {result.problems.length > 0 && (
            <ul>
              {result.problems.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </form>
  );
}

export interface SyncPanelProps {
  report: SyncReport | null;
  onSync: () => Promise<SyncReport>;
}

/** The role sync: what it last did, and a way to run it now. */
export function SyncPanel({ report, onSync }: SyncPanelProps) {
  const [shown, setShown] = useState<SyncReport | null>(report);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const last = shown ?? report;

  const now = () => {
    setBusy(true);
    setProblem(null);
    onSync()
      .then(setShown, (error: unknown) => setProblem(words(error)))
      .finally(() => setBusy(false));
  };

  return (
    <section className="panel" aria-label="Database roles">
      <div className="panel-head">
        <h2>Database roles</h2>
        <button type="button" onClick={now} disabled={busy}>
          {busy ? "Syncing…" : "Sync now"}
        </button>
      </div>
      <p className="hint">
        Everyone in a group above is a role in the retail database, made by the role sync: that is what they sign in
        as, and what their questions run as.
      </p>
      {problem && (
        <p className="notice notice-error" role="alert">
          {problem}
        </p>
      )}
      {last ? (
        <div className={last.ok ? "notice notice-quiet" : "notice notice-error"} role="status">
          <p>
            {last.people} people at {last.at}
            {last.created.length > 0 && `; made ${last.created.join(", ")}`}
            {last.removed.length > 0 && `; removed ${last.removed.join(", ")}`}
          </p>
          {[...last.conflicts, ...last.skipped, ...last.errors].map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      ) : (
        <p className="quiet">The sync has not run yet.</p>
      )}
    </section>
  );
}

export interface StatusBarProps {
  meta: DirectoryMeta | null;
  error: string | null;
}

export function StatusBar({ meta, error }: StatusBarProps) {
  return (
    <footer className="status-bar" role="contentinfo">
      {error ? (
        <span className="bad">{error}</span>
      ) : meta ? (
        <span>
          {meta.mode} directory {meta.base_dn} · {meta.people} people · nl2sql auth {meta.version}
        </span>
      ) : (
        <span>Connecting…</span>
      )}
    </footer>
  );
}
