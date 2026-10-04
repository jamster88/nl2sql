/**
 * The directory's web interface: who may sign in to the nl2sql stack, and in
 * which groups.
 *
 * Only for a standalone directory, and only for nl2sql-admins. A replica's
 * people are its primary's: this page is not served beside one, and the auth
 * service has no directory routes in that mode.
 *
 * Every change is followed by a fresh read rather than a patch of what is on
 * screen, so the page shows what the directory now says -- including what
 * slapd's overlays did with it -- and the role sync is woken by the service
 * itself on every write.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { createClient, type Client } from "./api/client";
import type { DirectoryMeta, Person } from "./api/types";
import { NewPersonForm } from "./components/NewPerson";
import { ImportPanel, StatusBar, SyncPanel } from "./components/Panels";
import { People } from "./components/People";
import { PersonEditor } from "./components/PersonEditor";

export interface AppProps {
  client?: Client;
}

export function App({ client: given }: AppProps) {
  const client = useMemo(() => given ?? createClient(), [given]);
  const [meta, setMeta] = useState<DirectoryMeta | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [found, list] = await Promise.all([client.meta(), client.people()]);
      setMeta(found);
      setPeople(list.people);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [client]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const person = people.find((candidate) => candidate.uid === selected) ?? null;
  const groups = meta?.groups ?? [];
  const minimum = meta?.min_password_length ?? 12;

  return (
    <div className="app">
      <header className="masthead">
        <h1>
          NL2SQL <span className="masthead-sub">directory</span>
        </h1>
        <button type="button" onClick={() => setAdding(true)} disabled={adding}>
          Add a person
        </button>
      </header>

      <main className="layout">
        <People people={people} selected={selected} onSelect={(uid) => { setSelected(uid); setAdding(false); }} />

        <div className="side">
          {adding && (
            <NewPersonForm
              groups={groups}
              minimum={minimum}
              onCancel={() => setAdding(false)}
              onAdd={async (body) => {
                const created = await client.add(body);
                setAdding(false);
                setSelected(created.uid);
                await refresh();
              }}
            />
          )}
          {person && !adding && (
            <PersonEditor
              person={person}
              groups={groups}
              minimum={minimum}
              onClose={() => setSelected(null)}
              onSave={async (change) => {
                await client.change(person.uid, change);
                await refresh();
              }}
              onPassword={(password) => client.setPassword(person.uid, password)}
              onUnlock={async () => {
                await client.unlock(person.uid);
                await refresh();
              }}
              onRemove={async () => {
                await client.remove(person.uid);
                setSelected(null);
                await refresh();
              }}
            />
          )}
          <SyncPanel
            report={meta?.role_sync ?? null}
            onSync={async () => {
              const report = await client.sync();
              await refresh();
              return report;
            }}
          />
          <ImportPanel
            onImport={async (filename, content) => {
              const result = await client.importFile(filename, content);
              await refresh();
              return result;
            }}
          />
        </div>
      </main>

      <StatusBar meta={meta} error={error} />
    </div>
  );
}
