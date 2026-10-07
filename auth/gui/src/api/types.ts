/**
 * The auth service's directory routes, as TypeScript.
 *
 * Written by hand from `auth/nl2sql_auth/models.py`, field for field;
 * `tests/auth/test_directory_gui_contract.py` compares the two, so a field
 * added to one and not the other is a failing test rather than a page that
 * quietly shows nothing.
 */

export interface ApiErrorBody {
  error: { code: string; message: string; detail?: Record<string, unknown> | null };
}

export interface Person {
  uid: string;
  name: string;
  cn: string;
  sn: string;
  given_name: string;
  mail: string;
  display_name: string;
  groups: string[];
  locked: boolean;
}

export interface PersonList {
  people: Person[];
  count: number;
}

export interface NewPerson {
  uid: string;
  given_name?: string;
  surname?: string;
  display_name?: string;
  mail?: string;
  groups?: string[];
  password?: string | null;
}

export interface PersonChange {
  given_name?: string | null;
  surname?: string | null;
  display_name?: string | null;
  mail?: string | null;
  groups?: string[] | null;
}

export interface Group {
  name: string;
  /** The Postgres role membership grants, or null when no role is mapped. */
  role: string | null;
  members: string[];
}

export interface GroupList {
  groups: Group[];
}

export interface ImportRequest {
  filename: string;
  content: string;
}

export interface ImportResult {
  created: string[];
  updated: string[];
  passwords: string[];
  groups: string[];
  problems: string[];
}

export interface SyncReport {
  at: string;
  ok: boolean;
  people: number;
  created: string[];
  removed: string[];
  changed: string[];
  conflicts: string[];
  skipped: string[];
  errors: string[];
}

export interface DirectoryMeta {
  version: string;
  mode: string;
  base_dn: string;
  people: number;
  groups: Group[];
  min_password_length: number;
  role_sync: SyncReport | null;
}
