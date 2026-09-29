/**
 * The queries run from this browser, most recent first, one click from the
 * editor. Kept in this browser only; see `api/history.ts`.
 */

import type { HistoryEntry } from "../api/history";

export interface HistoryProps {
  entries: readonly HistoryEntry[];
  onPick: (entry: HistoryEntry) => void;
  onClear: () => void;
}

export function History({ entries, onPick, onClear }: HistoryProps) {
  return (
    <section className="history" aria-label="History">
      <div className="history-head">
        <h2 className="panel-title">History</h2>
        {entries.length > 0 && (
          <button type="button" className="button button-quiet button-small" onClick={onClear}>
            Clear history
          </button>
        )}
      </div>
      {entries.length === 0 ? (
        <p className="muted small">Queries you run are kept here, in this browser only.</p>
      ) : (
        <ul className="history-list">
          {entries.map((entry) => (
            <li key={`${entry.mode}:${entry.sql}`}>
              <button type="button" className="history-item" onClick={() => onPick(entry)} title={entry.sql}>
                <span className={`dot ${entry.accepted ? "dot-good" : "dot-bad"}`} aria-hidden="true" />
                <span className="mono small history-sql">{entry.sql}</span>
                <span className="muted small">{entry.mode}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
