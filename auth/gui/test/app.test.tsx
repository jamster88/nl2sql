import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { fakeClient, makeMeta, makePerson } from "./helpers";

describe("App", () => {
  it("lists the directory and says where it is connected", async () => {
    render(<App client={fakeClient()} />);
    expect(await screen.findByRole("button", { name: "alice" })).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toHaveTextContent("standalone directory dc=nl2sql,dc=local");
  });

  it("adds a person, then shows them chosen and the directory read again", async () => {
    const client = fakeClient();
    render(<App client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a person" }));
    expect(screen.getByRole("button", { name: "Add a person" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Login"), "zoe");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(client.add).toHaveBeenCalled());
    await waitFor(() => expect(client.people).toHaveBeenCalledTimes(2));
  });

  it("closes the new-person form on cancel, and choosing someone closes it too", async () => {
    render(<App client={fakeClient()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a person" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form", { name: "Add a person" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add a person" }));
    await userEvent.click(screen.getByRole("button", { name: "alice" }));
    expect(screen.queryByRole("form", { name: "Add a person" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Editing alice" })).toBeInTheDocument();
  });

  it("edits, sets a password, unlocks and removes the chosen person, reading the directory after each", async () => {
    const locked = makePerson({ locked: true });
    const client = fakeClient({ people: vi.fn().mockResolvedValue({ people: [locked], count: 1 }) });
    render(<App client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "alice" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(client.change).toHaveBeenCalledWith("alice", expect.objectContaining({ mail: "alice@example.com" })));
    await userEvent.type(screen.getByLabelText("New password"), "a-long-enough-one");
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    await waitFor(() => expect(client.setPassword).toHaveBeenCalledWith("alice", "a-long-enough-one"));
    await userEvent.click(screen.getByRole("button", { name: "Unlock" }));
    await waitFor(() => expect(client.unlock).toHaveBeenCalledWith("alice"));
    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    await userEvent.click(screen.getByRole("button", { name: "Remove alice" }));
    await waitFor(() => expect(client.remove).toHaveBeenCalledWith("alice"));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Editing alice" })).not.toBeInTheDocument());
  });

  it("closes the editor", async () => {
    render(<App client={fakeClient()} />);
    await userEvent.click(await screen.findByRole("button", { name: "alice" }));
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("region", { name: "Editing alice" })).not.toBeInTheDocument();
  });

  it("runs the sync and loads a file, reading the directory after each", async () => {
    const client = fakeClient();
    render(<App client={client} />);
    await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(client.sync).toHaveBeenCalled());
    await userEvent.type(screen.getByLabelText("File contents"), "uid");
    await userEvent.click(screen.getByRole("button", { name: "Load" }));
    await waitFor(() => expect(client.importFile).toHaveBeenCalledWith("people.csv", "uid"));
    await waitFor(() => expect(client.meta).toHaveBeenCalledTimes(3));
  });

  it("says what went wrong when the directory cannot be read, in words or as thrown", async () => {
    const { unmount } = render(<App client={fakeClient({ meta: vi.fn().mockRejectedValue(new Error("cannot reach the directory service")) })} />);
    expect(await screen.findByText("cannot reach the directory service")).toBeInTheDocument();
    unmount();
    render(<App client={fakeClient({ people: vi.fn().mockRejectedValue("odd") })} />);
    expect(await screen.findByText("odd")).toBeInTheDocument();
  });

  it("uses the defaults of a fresh directory before the meta arrives", async () => {
    render(<App client={fakeClient({ meta: vi.fn().mockResolvedValue(makeMeta({ role_sync: null })) })} />);
    expect(await screen.findByText("The sync has not run yet.")).toBeInTheDocument();
  });

  it("builds its own client when none is given", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
    render(<App />);
    expect(await screen.findByText(/cannot reach the directory service/)).toBeInTheDocument();
    spy.mockRestore();
  });
});
