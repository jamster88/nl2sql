/**
 * Adding someone: a login, their names, their groups, and a first password
 * to hand them -- which they can change once they have signed in.
 */

import { useState, type FormEvent } from "react";

import type { Group, NewPerson } from "../api/types";
import { generatePassword } from "./password";

export interface NewPersonFormProps {
  groups: Group[];
  minimum: number;
  onAdd: (person: NewPerson) => Promise<void>;
  onCancel: () => void;
}

export function NewPersonForm({ groups, minimum, onAdd, onCancel }: NewPersonFormProps) {
  const [uid, setUid] = useState("");
  const [givenName, setGivenName] = useState("");
  const [surname, setSurname] = useState("");
  const [mail, setMail] = useState("");
  const [chosen, setChosen] = useState<string[]>(["nl2sql-users"]);
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const toggle = (name: string) =>
    setChosen((current) => (current.includes(name) ? current.filter((g) => g !== name) : [...current, name]));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (password && password.length < minimum) {
      setProblem(`A password must be at least ${minimum} characters.`);
      return;
    }
    setBusy(true);
    setProblem(null);
    onAdd({
      uid: uid.trim(),
      given_name: givenName.trim(),
      surname: surname.trim(),
      mail: mail.trim(),
      groups: chosen.filter((name) => groups.some((group) => group.name === name)),
      password: password || null,
    })
      .catch((error: unknown) => setProblem(error instanceof Error ? error.message : String(error)))
      .finally(() => setBusy(false));
  };

  return (
    <form className="panel editor" onSubmit={submit} aria-label="Add a person">
      <h2>Add a person</h2>
      {problem && (
        <p className="notice notice-error" role="alert">
          {problem}
        </p>
      )}
      <label>
        Login
        <input required aria-describedby="login-hint" value={uid} onChange={(event) => setUid(event.target.value)} />
      </label>
      <span id="login-hint" className="hint">
        Lower case letters, digits, “.”, “_” and “-”: it is also their database role.
      </span>
      <label>
        Given name
        <input value={givenName} onChange={(event) => setGivenName(event.target.value)} />
      </label>
      <label>
        Surname
        <input value={surname} onChange={(event) => setSurname(event.target.value)} />
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
          </label>
        ))}
      </fieldset>
      <label>
        First password
        <input
          aria-describedby="password-hint"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="new-password"
        />
      </label>
      <span id="password-hint" className="hint">
        Empty leaves them unable to sign in until one is set.{" "}
        <button type="button" className="link" onClick={() => setPassword(generatePassword())}>
          Generate one
        </button>
      </span>
      <div className="actions">
        <button type="submit" disabled={busy}>
          {busy ? "Adding…" : "Add"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
