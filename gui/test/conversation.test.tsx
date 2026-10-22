/**
 * The page's conversation (8.0): the id it makes and sends, the button that
 * makes another, the "Read as" line under a follow-up, and the status bar's
 * sentence about what the server remembers.
 */

import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "../src/App";
import { memorySentence, newConversationId, remembers } from "../src/api/conversation";
import { AnswerView } from "../src/components/AnswerView";
import { AskBox } from "../src/components/AskBox";
import { StatusBar } from "../src/components/StatusBar";
import { createFeedbackStore } from "../src/feedback/store";
import type { Conversation, Meta } from "../src/api/types";
import { FakeEventSource, fakeClient, fakeEventSource, makeAnswer, makeJob, makeMeta, memoryStorage } from "./helpers";

afterEach(cleanup);
beforeEach(() => {
  FakeEventSource.reset();
});

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const STANDALONE = "How many stores are there in each state?";

/** A server that remembers `turns` answered turns, as `/v1/meta` says it. */
function remembering(turns: number, version = "v7_0_0"): Meta {
  const base = makeMeta();
  return makeMeta({
    pipeline: {
      ...base.pipeline,
      version,
      memory: { turns, rows: 3, max_conversations: 200, ttl_seconds: 3600 },
    },
  });
}

function makeConversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: "c-1",
    turn: 2,
    remembered: 1,
    asked: "And in each state?",
    read_as: STANDALONE,
    resolved: true,
    reason: "",
    ...overrides,
  };
}

const store = () => createFeedbackStore(memoryStorage());

describe("newConversationId", () => {
  it("is the platform's UUID where there is one", () => {
    const randomUUID = vi.fn(() => "11111111-2222-4333-8444-555555555555" as const);
    expect(newConversationId({ randomUUID, getRandomValues: vi.fn() })).toBe("11111111-2222-4333-8444-555555555555");
  });

  it("makes the same 122 random bits from getRandomValues outside a secure context", () => {
    // Every byte all ones: the version and the variant are the only bits set
    // by the function rather than the source.
    const getRandomValues = <T extends ArrayBufferView | null>(array: T): T => {
      (array as unknown as Uint8Array).fill(0xff);
      return array;
    };
    const id = newConversationId({ getRandomValues });
    expect(id).toBe("ffffffff-ffff-4fff-bfff-ffffffffffff");
    expect(id).toMatch(UUID);
  });

  it("is a fresh UUID each time from the browser's own crypto", () => {
    const first = newConversationId();
    expect(first).toMatch(UUID);
    expect(newConversationId()).not.toBe(first);
  });
});

describe("what the server remembers", () => {
  it("is nothing before /v1/meta, from a server older than 8.0, or at CONVERSATION_MEMORY=0", () => {
    expect(remembers(null)).toBe(0);
    expect(remembers(makeMeta())).toBe(0);
    expect(remembers(remembering(0))).toBe(0);
    expect(remembers(remembering(5))).toBe(5);
  });

  it("is said in the status bar's words", () => {
    expect(memorySentence(remembering(5))).toBe("remembers the last 5 turns");
    expect(memorySentence(remembering(1))).toBe("remembers the last turn");
    expect(memorySentence(remembering(0))).toBe("each question asked alone");
    expect(memorySentence(makeMeta())).toBe("each question asked alone");
  });

  it("is in the status bar, with the pipeline's name", () => {
    render(<StatusBar meta={remembering(5, "v6_3_1")} error={null} warnings={[]} />);
    expect(screen.getByText("remembers the last 5 turns")).toBeInTheDocument();
    expect(screen.getByText("pipeline v6_3_1")).toBeInTheDocument();
  });

  it("leaves the pipeline unnamed for a server that does not name it", () => {
    render(<StatusBar meta={makeMeta()} error={null} warnings={[]} />);
    expect(screen.getByText("each question asked alone")).toBeInTheDocument();
    expect(screen.queryByText(/^pipeline /)).toBeNull();
  });
});

describe("the New conversation button", () => {
  it("is drawn only when there is a conversation to leave", () => {
    const { rerender } = render(<AskBox onAsk={vi.fn()} onCancel={vi.fn()} busy={false} showExamples={false} />);
    expect(screen.queryByRole("button", { name: "New conversation" })).toBeNull();

    const onNew = vi.fn();
    rerender(<AskBox onAsk={vi.fn()} onCancel={vi.fn()} busy={false} showExamples={false} onNewConversation={onNew} />);
    return userEvent.click(screen.getByRole("button", { name: "New conversation" })).then(() => {
      expect(onNew).toHaveBeenCalledOnce();
    });
  });

  it("waits while a question is being answered, as the box does", () => {
    render(<AskBox onAsk={vi.fn()} onCancel={vi.fn()} busy showExamples={false} onNewConversation={vi.fn()} />);
    expect(screen.getByRole("button", { name: "New conversation" })).toBeDisabled();
  });
});

describe("Read as", () => {
  const pipeline = makeMeta().pipeline;

  it("is under the words typed when a follow-up was restated, with where it stands", () => {
    const job = makeJob({ question: "And in each state?", answer: makeAnswer({ conversation: makeConversation() }) });
    const { container } = render(<AnswerView job={job} store={store()} pipeline={pipeline} />);

    const line = container.querySelector(".answer-read-as");
    expect(line).toHaveTextContent(`Read as: ${STANDALONE} · turn 2, 1 remembered`);
    expect(within(line as HTMLElement).getByText(STANDALONE).tagName).toBe("EM");
    expect(screen.getByRole("heading", { name: "And in each state?" })).toBeInTheDocument();
  });

  it.each([
    ["asked alone", null],
    ["in a conversation, not restated", makeConversation({ read_as: "", resolved: false, reason: "not restated" })],
    ["its restatement discarded", makeConversation({ resolved: false, reason: "C1 numbers: 5" })],
    ["resolved with no reading", makeConversation({ read_as: "" })],
  ])("is not there for a question %s", (_, conversation) => {
    const job = makeJob({ answer: makeAnswer({ conversation }) });
    const { container } = render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
    expect(container.querySelector(".answer-read-as")).toBeNull();
  });

  it("is shown as text, never as markup, since it is a model's sentence", () => {
    const reading = "<img src=x onerror=alert(1)> How many stores?";
    const job = makeJob({ answer: makeAnswer({ conversation: makeConversation({ read_as: reading }) }) });
    const { container } = render(<AnswerView job={job} store={store()} pipeline={pipeline} />);
    expect(container.querySelector(".answer-read-as img")).toBeNull();
    expect(container.querySelector(".answer-read-as em")?.textContent).toBe(reading);
  });

  it("is not taken for the answer when there are no claims to build the sentence from", () => {
    const answer = makeAnswer({
      claims: [],
      narrative: "",
      answer: `Read as: *${STANDALONE}*\n\nThere are stores in 12 states.\n\n| state | stores |`,
      conversation: makeConversation(),
    });
    const { container } = render(<AnswerView job={makeJob({ answer })} store={store()} pipeline={pipeline} />);
    expect(container.querySelector(".answer-headline")?.textContent).toBe("There are stores in 12 states.");
  });
});

describe("the page's conversation", () => {
  const watch = { eventSource: fakeEventSource };

  async function askAndAnswer(question: string, id: string): Promise<void> {
    const before = FakeEventSource.instances.length;
    await userEvent.type(screen.getByLabelText(/Ask the retail database/), `${question}{Enter}`);
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(before + 1));
    act(() => {
      FakeEventSource.last().emit("done", makeJob({ id, question }));
    });
    await screen.findByRole("heading", { name: question });
  }

  function conversations(ask: ReturnType<typeof vi.fn>): (string | undefined)[] {
    return ask.mock.calls.map((call) => (call[1] as { conversation?: string } | undefined)?.conversation);
  }

  it("sends one id with every question, and a new one after the button", async () => {
    const ask = vi.fn().mockResolvedValue(makeJob({ status: "queued", answer: null }));
    const client = fakeClient({ meta: vi.fn().mockResolvedValue(remembering(5)), ask });
    render(<App client={client} store={store()} watch={watch} />);
    await screen.findByText("remembers the last 5 turns");

    await askAndAnswer("How many stores are there?", "first");
    await askAndAnswer("And in each state?", "second");
    const [first, second] = conversations(ask);
    expect(first).toMatch(UUID);
    expect(second).toBe(first);

    await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(screen.getByText(/A new conversation: the next question is read without the ones before it/))
      .toBeInTheDocument();
    // The session list is the jobs answered, not the conversation.
    expect(within(screen.getByLabelText("Earlier questions")).getAllByRole("button")).toHaveLength(2);

    await askAndAnswer("How many products are there?", "third");
    const third = conversations(ask)[2];
    expect(third).toMatch(UUID);
    expect(third).not.toBe(first);
    expect(screen.queryByText(/A new conversation/)).toBeNull();
  });

  it("names none, and offers no button, when the server keeps no conversation", async () => {
    const ask = vi.fn().mockResolvedValue(makeJob({ status: "queued", answer: null }));
    const client = fakeClient({ meta: vi.fn().mockResolvedValue(remembering(0)), ask });
    render(<App client={client} store={store()} watch={watch} />);
    await screen.findByText("each question asked alone");

    await askAndAnswer("How many stores are there?", "first");
    expect(ask).toHaveBeenLastCalledWith("How many stores are there?");
    expect(screen.queryByRole("button", { name: "New conversation" })).toBeNull();
  });

  it("says a question refused because the last is still answering as it says any refusal", async () => {
    const { ApiError } = await import("../src/api/client");
    const busy = new ApiError(409, "conversation_busy",
      "conversation c-1 is still answering its last question; ask the next once that one has finished");
    const client = fakeClient({ meta: vi.fn().mockResolvedValue(remembering(5)), ask: vi.fn().mockRejectedValue(busy) });
    render(<App client={client} store={store()} watch={watch} />);
    await screen.findByText("remembers the last 5 turns");

    await userEvent.type(screen.getByLabelText(/Ask the retail database/), "and again?{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("That question could not be sent.");
    expect(alert.textContent).toContain("still answering its last question");
  });
});
