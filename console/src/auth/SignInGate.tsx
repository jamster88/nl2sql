/**
 * The page behind a sign-in, when the stack has one.
 *
 * Asks the auth service who is signed in, then shows one of four things:
 *
 * * the page, with who is signed in and a way out above it;
 * * the sign-in form, when nobody is -- or when the page's own API calls
 *   start answering 401, because a session ended while the page was open;
 * * a refusal naming the group the page needs, for someone signed in who is
 *   not in it -- with the way out, so they can sign in as somebody who is;
 * * just the page, when the stack has no sign-in at all (no auth service
 *   answers), which is what it was before there was any.
 *
 * The page itself is mounted only once someone may use it, and stays mounted
 * through a re-sign-in, so a session that ends mid-question does not lose
 * the question.
 */

import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";

import {
  createSessionClient,
  SignInError,
  UNAUTHORIZED_EVENT,
  type AuthMeta,
  type Session,
  type SessionClient,
} from "./session";

type State =
  | { kind: "checking" }
  | { kind: "off" }
  | { kind: "signed-out"; reason?: string }
  | { kind: "signed-in"; session: Session };

export interface SignInGateProps {
  children: ReactNode;
  /** What this page is called on the sign-in form. */
  title: string;
  /** The roles any one of which this page needs; empty for any signed-in person. */
  needs?: string[];
  /** The directory group a missing role comes from, for the refusal. */
  group?: string;
  client?: SessionClient;
}

function message(error: unknown): string {
  if (error instanceof SignInError) {
    if (error.code === "too_many_attempts" && error.retryAfter) {
      const minutes = Math.ceil(error.retryAfter / 60);
      return `Too many wrong passwords. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
    }
    return error.message;
  }
  return String(error);
}

function SignInForm({
  title,
  reason,
  client,
  onSignedIn,
}: {
  title: string;
  reason?: string | undefined;
  client: SessionClient;
  onSignedIn: (session: Session) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    client
      .signIn(username, password)
      .then(onSignedIn)
      .catch((error: unknown) => {
        setProblem(message(error));
        setPassword("");
      })
      .finally(() => setBusy(false));
  };

  return (
    <main className="signin">
      <form className="signin-form" onSubmit={submit} aria-label="Sign in">
        <h1>Sign in to {title}</h1>
        {reason && (
          <p className="notice notice-quiet" role="status">
            {reason}
          </p>
        )}
        {problem && (
          <p className="notice notice-error" role="alert">
            {problem}
          </p>
        )}
        <label>
          Name
          <input
            name="username"
            autoComplete="username"
            required
            autoFocus
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label>
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}

function PasswordDialog({
  client,
  minimum,
  onClose,
}: {
  client: SessionClient;
  minimum: number;
  onClose: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (next !== again) {
      setProblem("The two new passwords are not the same.");
      return;
    }
    if (next.length < minimum) {
      setProblem(`A password must be at least ${minimum} characters.`);
      return;
    }
    setProblem(null);
    client
      .changePassword(current, next)
      .then(() => setDone(true))
      .catch((error: unknown) => setProblem(message(error)));
  };

  return (
    <div className="signin-dialog" role="dialog" aria-label="Change your password">
      {done ? (
        <>
          <p role="status">Your password has been changed.</p>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </>
      ) : (
        <form onSubmit={submit}>
          {problem && (
            <p className="notice notice-error" role="alert">
              {problem}
            </p>
          )}
          <label>
            Current password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
            />
          </label>
          <label>
            New password
            <input
              type="password"
              autoComplete="new-password"
              required
              value={next}
              onChange={(event) => setNext(event.target.value)}
            />
          </label>
          <label>
            New password again
            <input
              type="password"
              autoComplete="new-password"
              required
              value={again}
              onChange={(event) => setAgain(event.target.value)}
            />
          </label>
          <div className="signin-dialog-actions">
            <button type="submit">Change password</button>
            <button type="button" onClick={onClose}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function UserBar({
  session,
  meta,
  client,
  onSignOut,
}: {
  session: Session;
  meta: AuthMeta | null;
  client: SessionClient;
  onSignOut: () => void;
}) {
  const [changing, setChanging] = useState(false);
  return (
    <div className="signin-bar" aria-label="Signed in">
      <span>
        Signed in as <strong>{session.name || session.user}</strong>
      </span>
      {meta?.directory_editable && (
        <button type="button" className="link" onClick={() => setChanging(true)}>
          Change password
        </button>
      )}
      <button type="button" className="link" onClick={onSignOut}>
        Sign out
      </button>
      {changing && meta && (
        <PasswordDialog client={client} minimum={meta.min_password_length} onClose={() => setChanging(false)} />
      )}
    </div>
  );
}

export function SignInGate({ children, title, needs = [], group, client: given }: SignInGateProps) {
  const client = useMemo(() => given ?? createSessionClient(), [given]);
  const [state, setState] = useState<State>({ kind: "checking" });
  const [meta, setMeta] = useState<AuthMeta | null>(null);

  useEffect(() => {
    let live = true;
    client.session().then((answer) => {
      if (!live) return;
      setState(answer.kind === "signed-in" ? { kind: "signed-in", session: answer.session } : { kind: answer.kind });
    });
    client.meta().then((value) => {
      if (live) setMeta(value);
    });
    return () => {
      live = false;
    };
  }, [client]);

  // A page's API call answering 401: the session ended while the page was
  // open (it expired, or the person was removed from the directory).
  useEffect(() => {
    const ended = () =>
      setState((current) =>
        current.kind === "signed-in"
          ? { kind: "signed-out", reason: "Your session has ended. Sign in again to carry on." }
          : current,
      );
    globalThis.addEventListener(UNAUTHORIZED_EVENT, ended);
    return () => globalThis.removeEventListener(UNAUTHORIZED_EVENT, ended);
  }, []);

  const signOut = useCallback(() => {
    client
      .signOut()
      .catch(() => undefined)
      .finally(() => setState({ kind: "signed-out" }));
  }, [client]);

  if (state.kind === "checking") {
    return (
      <p className="signin-checking" role="status">
        Checking who you are…
      </p>
    );
  }
  if (state.kind === "off") return <>{children}</>;
  if (state.kind === "signed-out") {
    return (
      <SignInForm
        title={title}
        reason={state.reason}
        client={client}
        onSignedIn={(session) => setState({ kind: "signed-in", session })}
      />
    );
  }

  const allowed = needs.length === 0 || needs.some((role) => state.session.roles.includes(role));
  return (
    <>
      <UserBar session={state.session} meta={meta} client={client} onSignOut={signOut} />
      {allowed ? (
        children
      ) : (
        <main className="signin">
          <div className="notice notice-error" role="alert">
            <strong>{state.session.name || state.session.user} cannot use {title}.</strong>
            <p>
              It needs a member of {group ?? needs.join(" or ")}. Ask a directory administrator to add you, or sign
              out and sign in as someone who is.
            </p>
          </div>
        </main>
      )}
    </>
  );
}
