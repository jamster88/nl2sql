/**
 * One person: their details and groups, a new password, a lockout cleared,
 * or them removed.
 *
 * Removing takes two clicks, the second on a button that names who: there
 * is no undo, and a browser's confirm dialog is the kind of thing people
 * click through.
 */

import { useEffect, useState, type FormEvent } from "react";

import type { Group, Person, PersonChange } from "../api/types";
import { generatePassword } from "./password";

export interface PersonEditorProps {
  person: Person;
  groups: Group[];
  minimum: number;
  onSave: (change: PersonChange) => Promise<void>;
  onPassword: (password: string) => Promise<void>;
  onUnlock: () => Promise<void>;
  onRemove: () => Promise<void>;
  onClose: () => void;
}

function words(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function PersonEditor({ person, groups, minimum, onSave, onPassword, onUnlock, onRemove, onClose }: PersonEditorProps) {
  const [givenName, setGivenName] = useState(person.given_name);
  const [surname, setSurname] = useState(person.sn);
  const [displayName, setDisplayName] = useState(person.display_name);
  const [mail, setMail] = useState(person.mail);
  const [chosen, setChosen] = useState<string[]>(person.groups);
  const [password, setPassword] = useState("");
  const [removing, setRemoving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  // Another person chosen: start again from theirs.
  useEffect(() => {
    setGivenName(person.given_name);
    setSurname(person.sn);
    setDisplayName(person.display_name);
    setMail(person.mail);
    setChosen(person.groups);
    setPassword("");
    setRemoving(false);
    setProblem(null);
    setDone(null);
  }, [person]);

  const run = (work: Promise<void>, said: string) => {
    setProblem(null);
    setDone(null);
    work.then(() => setDone(said)).catch((error: unknown) => setProblem(words(error)));
  };

  const toggle = (name: string) =>
    setChosen((current) => (current.includes(name) ? current.filter((g) => g !== name) : [...current, name]));

  const save = (event: FormEvent) => {
    event.preventDefault();
    run(onSave({ given_name: givenName, surname, display_name: displayName, mail, groups: chosen }), "Saved.");
  };

  const setNewPassword = (event: FormEvent) => {
    event.preventDefault();
    if (password.length < minimum) {
      setProblem(`A password must be at least ${minimum} characters.`);
      return;
    }
    run(onPassword(password), "Password set. Hand it to them; they can change it once signed in.");
  };

  return (
    <section className="panel editor" aria-label={`Editing ${person.uid}`}>
      <div className="panel-head">
        <h2>{person.uid}</h2>
        <button type="button" className="link" onClick={onClose}>
          Close
        </button>
      </div>
      {problem && (
        <p className="notice notice-error" role="alert">
          {problem}
        </p>
      )}
      {done && (
        <p className="notice notice-good" role="status">
          {done}
        </p>
      )}
      {person.locked && (
        <div className="notice notice-error">
          <p>Locked out after too many wrong passwords.</p>
          <button type="button" onClick={() => run(onUnlock(), "Unlocked.")}>
            Unlock
          </button>
        </div>
      )}

      <form onSubmit={save} aria-label="Details">
        <label>
          Given name
          <input value={givenName} onChange={(event) => setGivenName(event.target.value)} />
        </label>
        <label>
          Surname
          <input value={surname} onChange={(event) => setSurname(event.target.value)} />
        </label>
        <label>
          Display name
          <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
        </label>
        <label>
          Mail
          <input type="email" value={mail} onChange={(event) => setMail(event.target.value)} />
        </label>
        <fieldset>
          <legend>Groups</legend>
          {groups.map((group) => (
            <label key={group.name} className="check">
              <input type="checkbox" checked={chosen.includes(group.name)} onChange={() => toggle(group.name)} />
              {group.name}
              {group.role && <span className="hint"> ({group.role})</span>}
            </label>
          ))}
        </fieldset>
        <button type="submit">Save</button>
      </form>

      <form onSubmit={setNewPassword} aria-label="Set a new password">
        <label>
          New password
          <input value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" />
        </label>
        <div className="actions">
          <button type="submit">Set password</button>
          <button type="button" className="link" onClick={() => setPassword(generatePassword())}>
            Generate one
          </button>
        </div>
      </form>

      <div className="danger">
        {removing ? (
          <>
            <p>
              Remove <strong>{person.uid}</strong>? They lose every group and can no longer sign in. This cannot be
              undone.
            </p>
            <div className="actions">
              <button type="button" className="button-bad" onClick={() => run(onRemove(), "Removed.")}>
                Remove {person.uid}
              </button>
              <button type="button" onClick={() => setRemoving(false)}>
                Keep them
              </button>
            </div>
          </>
        ) : (
          <button type="button" className="button-bad" onClick={() => setRemoving(true)}>
            Remove…
          </button>
        )}
      </div>
    </section>
  );
}
