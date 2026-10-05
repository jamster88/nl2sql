import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { NewPersonForm } from "../src/components/NewPerson";
import { ImportPanel, StatusBar, SyncPanel } from "../src/components/Panels";
import { People } from "../src/components/People";
import { PersonEditor } from "../src/components/PersonEditor";
import { generatePassword } from "../src/components/password";
import { GROUPS, makeMeta, makePerson, makeSync } from "./helpers";

describe("generatePassword", () => {
  it("draws characters that cannot be mistaken for each other", () => {
    const password = generatePassword();
    expect(password).toHaveLength(20);
    expect(password).toMatch(/^[a-km-np-zA-HJ-NP-Z2-9]+$/);
  });

  it("skips bytes that would bias the alphabet", () => {
    let call = 0;
    const random = (bytes: Uint8Array) => {
      call += 1;
      bytes.fill(call === 1 ? 255 : 0); // all rejected, then all the first letter
      return bytes;
    };
    expect(generatePassword(4, random)).toBe("aaaa");
    expect(call).toBe(2);
  });
});

describe("People", () => {
  const people = [makePerson({ uid: "admin", name: "Admin", groups: ["nl2sql-admins"] }), makePerson({ locked: true })];

  it("lists everyone with their groups and marks a lockout", () => {
    render(<People people={people} selected="alice" onSelect={vi.fn()} />);
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(rows[2]).toHaveAttribute("aria-selected", "true");
    expect(within(rows[2]!).getByText("locked")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("nl2sql-admins")).toBeInTheDocument();
  });

  it("filters by any of login, name, mail or group, and says when nothing matches", async () => {
    const onSelect = vi.fn();
    render(<People people={people} selected={null} onSelect={onSelect} />);
    const filter = screen.getByRole("searchbox", { name: "Filter people" });
    await userEvent.type(filter, "REVIEWERS");
    expect(screen.getAllByRole("row")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "alice" }));
    expect(onSelect).toHaveBeenCalledWith("alice");
    await userEvent.clear(filter);
    await userEvent.type(filter, "nobody-like-this");
    expect(screen.getByText("Nobody matches that.")).toBeInTheDocument();
  });

  it("says when the directory is empty", () => {
    render(<People people={[]} selected={null} onSelect={vi.fn()} />);
    expect(screen.getByText("Nobody is in the directory yet.")).toBeInTheDocument();
  });
});

describe("NewPersonForm", () => {
  it("adds someone with the groups chosen and a generated password", async () => {
    const onAdd = vi.fn().mockResolvedValue(undefined);
    render(<NewPersonForm groups={GROUPS} minimum={12} onAdd={onAdd} onCancel={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Login"), " zoe ");
    await userEvent.type(screen.getByLabelText("Given name"), "Zoe");
    await userEvent.type(screen.getByLabelText("Surname"), "Ng");
    await userEvent.type(screen.getByLabelText("Mail"), "zoe@example.com");
    await userEvent.click(screen.getByLabelText("nl2sql-reviewers"));
    await userEvent.click(screen.getByLabelText("sales"));
    await userEvent.click(screen.getByLabelText("sales"));
    await userEvent.click(screen.getByRole("button", { name: "Generate one" }));
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    const [body] = onAdd.mock.calls[0]!;
    expect(body).toMatchObject({ uid: "zoe", given_name: "Zoe", surname: "Ng", mail: "zoe@example.com" });
    expect(body.groups).toEqual(["nl2sql-users", "nl2sql-reviewers"]);
    expect(body.password).toHaveLength(20);
  });

  it("leaves out a chosen group the directory no longer has, and no password means none", async () => {
    const onAdd = vi.fn().mockResolvedValue(undefined);
    render(<NewPersonForm groups={GROUPS.filter((g) => g.name !== "nl2sql-users")} minimum={12} onAdd={onAdd} onCancel={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Login"), "zoe");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onAdd.mock.calls[0]![0]).toMatchObject({ groups: [], password: null });
  });

  it("refuses a short password before asking, and shows what the service refused", async () => {
    const onAdd = vi.fn().mockRejectedValueOnce(new Error("zoe already exists")).mockRejectedValueOnce("odd");
    render(<NewPersonForm groups={GROUPS} minimum={12} onAdd={onAdd} onCancel={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("Login"), "zoe");
    await userEvent.type(screen.getByLabelText("First password"), "short");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at least 12 characters");
    expect(onAdd).not.toHaveBeenCalled();
    await userEvent.clear(screen.getByLabelText("First password"));
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("zoe already exists");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("odd"));
  });

  it("can be cancelled", async () => {
    const onCancel = vi.fn();
    render(<NewPersonForm groups={GROUPS} minimum={12} onAdd={vi.fn()} onCancel={onCancel} />);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });
});

describe("PersonEditor", () => {
  function editor(overrides = {}, person = makePerson()) {
    const props = {
      person,
      groups: GROUPS,
      minimum: 12,
      onSave: vi.fn().mockResolvedValue(undefined),
      onPassword: vi.fn().mockResolvedValue(undefined),
      onUnlock: vi.fn().mockResolvedValue(undefined),
      onRemove: vi.fn().mockResolvedValue(undefined),
      onClose: vi.fn(),
      ...overrides,
    };
    return { props, ...render(<PersonEditor {...props} />) };
  }

  it("saves the details and groups as edited", async () => {
    const { props } = editor();
    await userEvent.clear(screen.getByLabelText("Given name"));
    await userEvent.type(screen.getByLabelText("Given name"), "Al");
    await userEvent.clear(screen.getByLabelText("Surname"));
    await userEvent.type(screen.getByLabelText("Surname"), "Smyth");
    await userEvent.clear(screen.getByLabelText("Mail"));
    await userEvent.type(screen.getByLabelText("Mail"), "a@x");
    await userEvent.type(screen.getByLabelText("Display name"), "Dr A");
    await userEvent.click(screen.getByLabelText(/nl2sql-reviewers/));
    await userEvent.click(screen.getByLabelText(/nl2sql-admins/));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(props.onSave).toHaveBeenCalledWith({
      given_name: "Al",
      surname: "Smyth",
      display_name: "Dr A",
      mail: "a@x",
      groups: ["nl2sql-users", "nl2sql-admins"],
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
    expect(screen.getByText("(nl2sql_admins)")).toBeInTheDocument();
  });

  it("sets a password, refusing a short one first", async () => {
    const { props } = editor();
    await userEvent.type(screen.getByLabelText("New password"), "short");
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at least 12 characters");
    await userEvent.click(screen.getByRole("button", { name: "Generate one" }));
    await userEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect(props.onPassword.mock.calls[0]![0]).toHaveLength(20);
    expect(await screen.findByRole("status")).toHaveTextContent("Password set.");
  });

  it("unlocks a locked-out person", async () => {
    const { props } = editor({}, makePerson({ locked: true }));
    expect(screen.getByText(/Locked out after too many wrong passwords/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Unlock" }));
    expect(props.onUnlock).toHaveBeenCalled();
    expect(await screen.findByRole("status")).toHaveTextContent("Unlocked.");
  });

  it("removes only after a second click that names who", async () => {
    const { props } = editor();
    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    await userEvent.click(screen.getByRole("button", { name: "Keep them" }));
    expect(props.onRemove).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Remove…" }));
    expect(screen.getByText(/This cannot be undone/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove alice" }));
    expect(props.onRemove).toHaveBeenCalled();
  });

  it("shows what the service refused, in its words or the thing thrown", async () => {
    editor({ onSave: vi.fn().mockRejectedValueOnce(new Error("you cannot take yourself out of nl2sql-admins")).mockRejectedValueOnce("odd") });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("you cannot take yourself out of nl2sql-admins");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("odd"));
  });

  it("starts again when another person is chosen, and can be closed", async () => {
    const { props, rerender } = editor();
    await userEvent.type(screen.getByLabelText("New password"), "half-typed");
    rerender(<PersonEditor {...props} person={makePerson({ uid: "bob", mail: "bob@x", groups: [] })} />);
    expect(screen.getByLabelText("New password")).toHaveValue("");
    expect(screen.getByLabelText("Mail")).toHaveValue("bob@x");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(props.onClose).toHaveBeenCalled();
  });
});

describe("ImportPanel", () => {
  it("loads a chosen file and reports what happened, problems and all", async () => {
    const onImport = vi.fn().mockResolvedValue({
      created: ["zoe"],
      updated: ["alice"],
      passwords: ["zoe"],
      groups: ["zoe -> nl2sql-users"],
      problems: ["line 3: 'bad name' is not a usable login name"],
    });
    render(<ImportPanel onImport={onImport} />);
    expect(screen.getByRole("button", { name: "Load" })).toBeDisabled();
    const file = new File(["uid,groups\nzoe,nl2sql-users\n"], "staff.csv", { type: "text/csv" });
    await userEvent.upload(screen.getByLabelText("File"), file);
    await waitFor(() => expect(screen.getByLabelText("File contents")).toHaveValue("uid,groups\nzoe,nl2sql-users\n"));
    expect(screen.getByLabelText("File name")).toHaveValue("staff.csv");
    await userEvent.click(screen.getByRole("button", { name: "Load" }));
    expect(onImport).toHaveBeenCalledWith("staff.csv", "uid,groups\nzoe,nl2sql-users\n");
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("1 added, 1 updated, 1 passwords set, 1 memberships added.");
    expect(status).toHaveTextContent("'bad name' is not a usable login name");
  });

  it("takes pasted text, and says when the load is refused or a file cannot be read", async () => {
    const onImport = vi
      .fn()
      .mockResolvedValueOnce({ created: [], updated: [], passwords: [], groups: [], problems: [] })
      .mockRejectedValueOnce(new Error("the directory is busy"))
      .mockRejectedValueOnce("odd");
    render(<ImportPanel onImport={onImport} />);
    await userEvent.clear(screen.getByLabelText("File name"));
    await userEvent.type(screen.getByLabelText("File name"), "export.ldif");
    await userEvent.type(screen.getByLabelText("File contents"), "uid");
    await userEvent.click(screen.getByRole("button", { name: "Load" }));
    expect(onImport).toHaveBeenCalledWith("export.ldif", "uid");
    expect(await screen.findByRole("status")).not.toHaveTextContent("problem");
    await userEvent.click(screen.getByRole("button", { name: "Load" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the directory is busy");
    await userEvent.click(screen.getByRole("button", { name: "Load" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("odd"));

    const broken = new File(["x"], "bad.csv");
    broken.text = () => Promise.reject(new Error("cannot read it"));
    await userEvent.upload(screen.getByLabelText("File"), broken);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("cannot read it"));
  });

  it("ignores a file chooser closed without a file", async () => {
    render(<ImportPanel onImport={vi.fn()} />);
    const input = screen.getByLabelText("File") as HTMLInputElement;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    expect(screen.getByLabelText("File name")).toHaveValue("people.csv");
  });
});

describe("SyncPanel", () => {
  it("shows the last sync, runs one now, and lists what it could not do", async () => {
    const onSync = vi
      .fn()
      .mockResolvedValueOnce(makeSync({ ok: false, created: ["zoe"], removed: ["bob (dropped)"], conflicts: ["analytics is already a role"], errors: ["x: denied"] }))
      .mockRejectedValueOnce(new Error("the sync is not configured"))
      .mockRejectedValueOnce("odd");
    render(<SyncPanel report={makeSync()} onSync={onSync} />);
    expect(screen.getByRole("status")).toHaveTextContent("2 people at 2026-10-03T12:00:00+00:00");
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("made zoe");
    expect(status).toHaveTextContent("removed bob (dropped)");
    expect(status).toHaveTextContent("analytics is already a role");
    expect(status).toHaveClass("notice-error");
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the sync is not configured");
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("odd"));
  });

  it("says when the sync has not run yet, and is busy while it runs", async () => {
    let finish: (report: ReturnType<typeof makeSync>) => void = () => undefined;
    render(<SyncPanel report={null} onSync={() => new Promise((resolve) => (finish = resolve))} />);
    expect(screen.getByText("The sync has not run yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sync now" }));
    expect(screen.getByRole("button", { name: "Syncing…" })).toBeDisabled();
    finish(makeSync());
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });
});

describe("StatusBar", () => {
  it("says what it is connected to, that it is connecting, or what went wrong", () => {
    const { rerender } = render(<StatusBar meta={null} error={null} />);
    expect(screen.getByRole("contentinfo")).toHaveTextContent("Connecting…");
    rerender(<StatusBar meta={makeMeta()} error={null} />);
    expect(screen.getByRole("contentinfo")).toHaveTextContent("standalone directory dc=nl2sql,dc=local · 2 people · nl2sql auth 6.1.0");
    rerender(<StatusBar meta={makeMeta()} error="cannot reach the directory service" />);
    expect(screen.getByRole("contentinfo")).toHaveTextContent("cannot reach the directory service");
  });
});
