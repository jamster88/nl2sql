/**
 * The draft helper.
 *
 * `toDraft` exists to stop React switching an input from uncontrolled to
 * controlled halfway through editing, which is what happens the first time
 * a field arrives as `undefined` and then gets typed into. So the cases
 * worth testing are all the shapes the server's JSONB column can actually
 * hold, including the ones nothing should ever put there.
 */

import { describe, expect, it } from "vitest";

import { EMPTY, JUDGEMENT, REQUIRED, missing, toDraft } from "../src/api/draft";
import { makeDraft } from "./helpers";

describe("toDraft", () => {
  it("fills every field when given nothing", () => {
    expect(toDraft(null)).toEqual(EMPTY);
    expect(toDraft(undefined)).toEqual(EMPTY);
    expect(toDraft({})).toEqual(EMPTY);
  });

  it("keeps what the server sent", () => {
    const draft = makeDraft();
    expect(toDraft(draft as unknown as Record<string, unknown>)).toEqual(draft);
  });

  it("drops a field that is not a string rather than binding an input to it", () => {
    const result = toDraft({ title: 42, question: "kept" });
    expect(result.title).toBe("");
    expect(result.question).toBe("kept");
  });

  it("stringifies extra meta values and ignores a non-object", () => {
    expect(toDraft({ extra_meta: { source: "gui", count: 3 } }).extra_meta).toEqual({
      source: "gui",
      count: "3",
    });
    expect(toDraft({ extra_meta: "nope" }).extra_meta).toEqual({});
    expect(toDraft({ extra_meta: ["a"] }).extra_meta).toEqual({});
    expect(toDraft({ extra_meta: null }).extra_meta).toEqual({});
  });
});

describe("missing", () => {
  it("is empty for a complete draft", () => {
    expect(missing(makeDraft())).toEqual([]);
  });

  it("names every empty required field, not just the first", () => {
    expect(missing(makeDraft({ keywords: "", result: "", title: "  " }))).toEqual([
      "title",
      "keywords",
      "result",
    ]);
  });

  it("does not require the optional fields", () => {
    expect(missing(makeDraft({ translation_note: "", suite: "" }))).toEqual([]);
  });
});

describe("the field lists", () => {
  it("marks the three fields a submission can never supply", () => {
    // If this changes, the review form stops telling a curator which fields
    // are actually their judgement rather than the agent's output.
    expect([...JUDGEMENT]).toEqual(["keywords", "reasoning_target", "result"]);
    for (const field of JUDGEMENT) expect(REQUIRED).toContain(field);
  });
});
