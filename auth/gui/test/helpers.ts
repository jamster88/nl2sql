/**
 * Fixtures and a fake client, so the page can be driven with no service.
 */

import { vi } from "vitest";

import type { Client } from "../src/api/client";
import type { DirectoryMeta, Group, Person, SyncReport } from "../src/api/types";

export function makePerson(overrides: Partial<Person> = {}): Person {
  return {
    uid: "alice",
    name: "Alice Smith",
    cn: "Alice Smith",
    sn: "Smith",
    given_name: "Alice",
    mail: "alice@example.com",
    display_name: "",
    groups: ["nl2sql-reviewers", "nl2sql-users"],
    locked: false,
    ...overrides,
  };
}

export const GROUPS: Group[] = [
  { name: "nl2sql-admins", role: "nl2sql_admins", members: ["admin"] },
  { name: "nl2sql-reviewers", role: "nl2sql_reviewers", members: ["alice"] },
  { name: "nl2sql-users", role: "nl2sql_users", members: ["alice", "admin"] },
  { name: "sales", role: null, members: [] },
];

export function makeSync(overrides: Partial<SyncReport> = {}): SyncReport {
  return {
    at: "2026-10-03T12:00:00+00:00",
    ok: true,
    people: 2,
    created: [],
    removed: [],
    changed: [],
    conflicts: [],
    skipped: [],
    errors: [],
    ...overrides,
  };
}

export function makeMeta(overrides: Partial<DirectoryMeta> = {}): DirectoryMeta {
  return {
    version: "6.1.0",
    mode: "standalone",
    base_dn: "dc=nl2sql,dc=local",
    people: 2,
    groups: GROUPS,
    min_password_length: 12,
    role_sync: makeSync(),
    ...overrides,
  };
}

export function fakeClient(overrides: Partial<Client> = {}): Client {
  const people = [makePerson({ uid: "admin", name: "Admin", groups: ["nl2sql-admins", "nl2sql-users"] }), makePerson()];
  return {
    meta: vi.fn().mockResolvedValue(makeMeta()),
    people: vi.fn().mockResolvedValue({ people, count: people.length }),
    person: vi.fn().mockResolvedValue(makePerson()),
    add: vi.fn().mockImplementation(async (body) => makePerson({ uid: body.uid })),
    change: vi.fn().mockResolvedValue(makePerson()),
    remove: vi.fn().mockResolvedValue(undefined),
    setPassword: vi.fn().mockResolvedValue(undefined),
    unlock: vi.fn().mockResolvedValue(undefined),
    groups: vi.fn().mockResolvedValue({ groups: GROUPS }),
    importFile: vi.fn().mockResolvedValue({ created: ["zoe"], updated: [], passwords: ["zoe"], groups: [], problems: [] }),
    sync: vi.fn().mockResolvedValue(makeSync({ created: ["zoe"] })),
    ...overrides,
  };
}
