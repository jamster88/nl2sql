/**
 * The queries run from this browser, most recent first.
 *
 * Kept in `localStorage`, which is per browser and per origin: this is a
 * convenience for the person at the keyboard -- the query from ten minutes
 * ago, one click away -- and not a record. Nothing here is sent anywhere.
 * Every read and write is guarded, because storage can be absent, full or
 * refused (a private window, a blocked origin), and a console that failed
 * to open because it could not remember is a worse tool than one that
 * forgets.
 */

import type { Mode } from "./types";

export const HISTORY_KEY = "nl2sql-console:history";
export const HISTORY_LIMIT = 25;

export interface HistoryEntry {
  sql: string;
  mode: Mode;
  /** ISO time it was run. */
  at: string;
  /** Whether the agent's gates would have let it through. */
  accepted: boolean;
}

function isEntry(value: unknown): value is HistoryEntry {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.sql === "string" &&
    (entry.mode === "run" || entry.mode === "plan" || entry.mode === "analyze") &&
    typeof entry.at === "string" &&
    typeof entry.accepted === "boolean"
  );
}

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function loadHistory(): HistoryEntry[] {
  try {
    const raw = storage()?.getItem(HISTORY_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(isEntry).slice(0, HISTORY_LIMIT) : [];
  } catch {
    return [];
  }
}

export function saveHistory(entries: readonly HistoryEntry[]): void {
  try {
    storage()?.setItem(HISTORY_KEY, JSON.stringify(entries));
  } catch {
    // Full or refused: the list still works for this page, it just will
    // not be here next time.
  }
}

/** `entry` at the front; an earlier run of the same SQL and mode dropped. */
export function remember(entries: readonly HistoryEntry[], entry: HistoryEntry): HistoryEntry[] {
  const rest = entries.filter((old) => !(old.sql === entry.sql && old.mode === entry.mode));
  return [entry, ...rest].slice(0, HISTORY_LIMIT);
}
