import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SignInGate } from "../src/auth/SignInGate";
import {
  createSessionClient,
  notifyUnauthorized,
  SignInError,
  UNAUTHORIZED_EVENT,
  type AuthMeta,
  type Session,
  type SessionAnswer,
  type SessionClient,
} from "../src/auth/session";

const ALICE: Session = { user: "alice", name: "Alice Smith", roles: ["nl2sql_users"], kind: "session", expires_at: 1 };
const META: AuthMeta = { version: "x", mode: "standalone", directory_editable: true, session_hours: 8, min_password_length: 12 };

function fakeClient(overrides: Partial<SessionClient> = {}, answer: SessionAnswer = { kind: "signed-out" }): SessionClient {
  return {
    session: vi.fn().mockResolvedValue(answer),
    meta: vi.fn().mockResolvedValue(META),
    signIn: vi.fn().mockResolvedValue(ALICE),
    signOut: vi.fn().mockResolvedValue(undefined),
    changePassword: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers });
}

describe("the session client", () => {
  it("asks who is signed in and tells the three answers apart", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(200, ALICE))
      .mockResolvedValueOnce(json(401, { error: { code: "sign_in_required", message: "sign in" } }))
      .mockResolvedValueOnce(json(502, null))
      .mockRejectedValueOnce(new TypeError("offline"));
    const client = createSessionClient({ fetch, baseUrl: "https://gui.example/" });
    expect(await client.session()).toEqual({ kind: "signed-in", session: ALICE });
    expect(await client.session()).toEqual({ kind: "signed-out" });
    expect(await client.session()).toEqual({ kind: "off" });
    expect(await client.session()).toEqual({ kind: "off" });
    expect(fetch).toHaveBeenCalledWith("https://gui.example/auth/session", expect.objectContaining({ credentials: "same-origin" }));
  });

  it("reads the sign-in metadata, or nothing", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(json(200, META)).mockResolvedValueOnce(json(404, null)).mockRejectedValueOnce(new TypeError("x"));
    const client = createSessionClient({ fetch });
    expect(await client.meta()).toEqual(META);
    expect(await client.meta()).toBeNull();
    expect(await client.meta()).toBeNull();
  });

  it("signs in with a JSON body and returns the session", async () => {
    const fetch = vi.fn().mockResolvedValue(json(200, ALICE));
    expect(await createSessionClient({ fetch }).signIn("alice", "pw")).toEqual(ALICE);
    const [path, init] = fetch.mock.calls[0]!;
    expect(path).toBe("/auth/login");
    expect(init).toMatchObject({ method: "POST", body: JSON.stringify({ username: "alice", password: "pw" }) });
    expect(init.headers).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
  });

  it("turns a refusal into an error with its code and how long to wait", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(429, { error: { code: "too_many_attempts", message: "wait" } }, { "Retry-After": "120" }))
      .mockResolvedValueOnce(new Response("<html>bad gateway</html>", { status: 500 }))
      .mockRejectedValueOnce(new TypeError("offline"));
    const client = createSessionClient({ fetch });
    await expect(client.signIn("a", "b")).rejects.toMatchObject({ status: 429, code: "too_many_attempts", retryAfter: 120 });
    await expect(client.signIn("a", "b")).rejects.toMatchObject({ status: 500, code: "error", message: "HTTP 500", retryAfter: null });
    await expect(client.signIn("a", "b")).rejects.toMatchObject({ status: 0, code: "unreachable" });
  });

  it("signs out and changes a password, and says when either is refused", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(204, undefined))
      .mockResolvedValueOnce(json(500, { error: { code: "x", message: "no" } }))
      .mockResolvedValueOnce(json(204, undefined))
      .mockResolvedValueOnce(json(403, { error: { code: "wrong_password", message: "not accepted" } }));
    const client = createSessionClient({ fetch });
    await client.signOut();
    await expect(client.signOut()).rejects.toBeInstanceOf(SignInError);
    await client.changePassword("old", "new-password");
    expect(fetch.mock.calls[2]![1].body).toBe(JSON.stringify({ current: "old", new: "new-password" }));
    await expect(client.changePassword("old", "new")).rejects.toMatchObject({ code: "wrong_password" });
  });

  it("uses the global fetch by default", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, META));
    expect(await createSessionClient().meta()).toEqual(META);
    spy.mockRestore();
  });

  it("raises the unauthorized event for whoever listens", () => {
    const heard = vi.fn();
    globalThis.addEventListener(UNAUTHORIZED_EVENT, heard);
    notifyUnauthorized();
    globalThis.removeEventListener(UNAUTHORIZED_EVENT, heard);
    expect(heard).toHaveBeenCalledOnce();
  });
});

describe("the sign-in gate", () => {
  it("says it is checking, then shows the page when sign-in is off", async () => {
    render(
      <SignInGate title="NL2SQL" client={fakeClient({}, { kind: "off" })}>
        <p>the page</p>
      </SignInGate>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Checking who you are");
    expect(await screen.findByText("the page")).toBeInTheDocument();
    expect(screen.queryByText(/Signed in as/)).not.toBeInTheDocument();
  });

  it("asks somebody signed out to sign in, and then shows the page", async () => {
    const client = fakeClient();
    render(
      <SignInGate title="NL2SQL" client={client}>
        <p>the page</p>
      </SignInGate>,
    );
    await userEvent.type(await screen.findByLabelText("Name"), "alice");
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("the page")).toBeInTheDocument();
    expect(client.signIn).toHaveBeenCalledWith("alice", "pw");
    expect(screen.getByLabelText("Signed in")).toHaveTextContent("Signed in as Alice Smith");
  });

  it("says why a sign-in failed and clears the password", async () => {
    const client = fakeClient({
      signIn: vi
        .fn()
        .mockRejectedValueOnce(new SignInError(401, "invalid_credentials", "that name and password were not accepted"))
        .mockRejectedValueOnce(new SignInError(429, "too_many_attempts", "wait", 60))
        .mockRejectedValueOnce(new SignInError(429, "too_many_attempts", "wait", 600))
        .mockRejectedValueOnce("odd"),
    });
    render(
      <SignInGate title="NL2SQL" client={client}>
        <p>the page</p>
      </SignInGate>,
    );
    const password = await screen.findByLabelText("Password");
    await userEvent.type(screen.getByLabelText("Name"), "alice");
    for (const expected of [
      "that name and password were not accepted",
      "Try again in 1 minute.",
      "Try again in 10 minutes.",
      "odd",
    ]) {
      await userEvent.type(password, "wrong");
      await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(expected);
      expect(password).toHaveValue("");
    }
  });

  it("shows the button busy while signing in", async () => {
    let finish: (session: Session) => void = () => undefined;
    const client = fakeClient({ signIn: vi.fn(() => new Promise<Session>((resolve) => (finish = resolve))) });
    render(
      <SignInGate title="NL2SQL" client={client}>
        <p>the page</p>
      </SignInGate>,
    );
    await userEvent.type(await screen.findByLabelText("Name"), "a");
    await userEvent.type(screen.getByLabelText("Password"), "b");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("button", { name: "Signing in…" })).toBeDisabled();
    await act(async () => finish(ALICE));
    expect(await screen.findByText("the page")).toBeInTheDocument();
  });

  it("refuses someone without the role the page needs, naming the group", async () => {
    render(
      <SignInGate title="the review page" needs={["nl2sql_reviewers"]} group="nl2sql-reviewers" client={fakeClient({}, { kind: "signed-in", session: ALICE })}>
        <p>the page</p>
      </SignInGate>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Alice Smith cannot use the review page.");
    expect(screen.getByRole("alert")).toHaveTextContent("It needs a member of nl2sql-reviewers.");
    expect(screen.queryByText("the page")).not.toBeInTheDocument();
  });

  it("names the roles when no group is given, and the user when there is no name", async () => {
    const nameless = { ...ALICE, name: "" };
    render(
      <SignInGate title="X" needs={["a", "b"]} client={fakeClient({}, { kind: "signed-in", session: nameless })}>
        <p>the page</p>
      </SignInGate>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("alice cannot use X.");
    expect(screen.getByRole("alert")).toHaveTextContent("It needs a member of a or b.");
    expect(screen.getByLabelText("Signed in")).toHaveTextContent("Signed in as alice");
  });

  it("goes back to the sign-in form when a page's call answers 401", async () => {
    render(
      <SignInGate title="NL2SQL" client={fakeClient({}, { kind: "signed-in", session: ALICE })}>
        <p>the page</p>
      </SignInGate>,
    );
    await screen.findByText("the page");
    act(() => notifyUnauthorized());
    expect(await screen.findByRole("status")).toHaveTextContent("Your session has ended");
  });

  it("ignores a 401 heard while nobody is signed in", async () => {
    render(
      <SignInGate title="NL2SQL" client={fakeClient()}>
        <p>the page</p>
      </SignInGate>,
    );
    await screen.findByLabelText("Name");
    act(() => notifyUnauthorized());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("signs out, even when the service could not be told", async () => {
    const client = fakeClient({ signOut: vi.fn().mockRejectedValue(new Error("down")) }, { kind: "signed-in", session: ALICE });
    render(
      <SignInGate title="NL2SQL" client={client}>
        <p>the page</p>
      </SignInGate>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByLabelText("Name")).toBeInTheDocument();
  });

  it("changes a password, checking the two new ones agree and are long enough first", async () => {
    const client = fakeClient(
      { changePassword: vi.fn().mockRejectedValueOnce(new SignInError(403, "wrong_password", "the current password was not accepted")).mockResolvedValueOnce(undefined) },
      { kind: "signed-in", session: ALICE },
    );
    render(
      <SignInGate title="NL2SQL" client={client}>
        <p>the page</p>
      </SignInGate>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Change password" }));
    const dialog = screen.getByRole("dialog", { name: "Change your password" });
    const submit = () => userEvent.click(within(dialog).getByRole("button", { name: "Change password" }));
    const [current, next, again] = [
      screen.getByLabelText("Current password"),
      screen.getByLabelText("New password"),
      screen.getByLabelText("New password again"),
    ];
    await userEvent.type(current, "old-password");
    await userEvent.type(next, "a-new-password");
    await userEvent.type(again, "a-different-one");
    await submit();
    expect(screen.getByRole("alert")).toHaveTextContent("not the same");
    await userEvent.clear(next);
    await userEvent.clear(again);
    await userEvent.type(next, "short");
    await userEvent.type(again, "short");
    await submit();
    expect(screen.getByRole("alert")).toHaveTextContent("at least 12 characters");
    await userEvent.clear(next);
    await userEvent.clear(again);
    await userEvent.type(next, "a-new-password");
    await userEvent.type(again, "a-new-password");
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("the current password was not accepted");
    await submit();
    expect(await screen.findByText("Your password has been changed.")).toBeInTheDocument();
    expect(client.changePassword).toHaveBeenLastCalledWith("old-password", "a-new-password");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(dialog).not.toBeInTheDocument();
  });

  it("offers no password change for a replica, and the dialog can be cancelled", async () => {
    const replica = fakeClient({ meta: vi.fn().mockResolvedValue({ ...META, directory_editable: false }) }, { kind: "signed-in", session: ALICE });
    const { unmount } = render(
      <SignInGate title="NL2SQL" client={replica}>
        <p>the page</p>
      </SignInGate>,
    );
    await screen.findByText("the page");
    await waitFor(() => expect(replica.meta).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Change password" })).not.toBeInTheDocument();
    unmount();

    render(
      <SignInGate title="NL2SQL" client={fakeClient({}, { kind: "signed-in", session: ALICE })}>
        <p>the page</p>
      </SignInGate>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Change password" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("builds its own client when none is given, and ignores answers after it is gone", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(502, null));
    const { unmount } = render(
      <SignInGate title="NL2SQL">
        <p>the page</p>
      </SignInGate>,
    );
    unmount();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    spy.mockRestore();
  });
});
