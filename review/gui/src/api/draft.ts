/**
 * The draft, as the form holds it.
 *
 * The server seeds a draft when a submission has none, so this module does
 * not duplicate that decision -- it only fills the gaps in whatever came
 * back, so a form never binds an input to `undefined` and React never
 * switches a field from uncontrolled to controlled halfway through editing.
 *
 * The client-side checks below are a convenience, not the contract. The
 * server refuses a draft that cannot become a golden pair, and refuses it
 * for reasons this file cannot know -- the next free pair id, whether the
 * existing document still parses, whether a round trip through the loader's
 * own parser gives back what went in. So these exist to grey out a button
 * and say why, and `preview` is what is believed.
 */

import type { DraftModel } from "./types";

/** The fields a pair cannot be built without, in the order the form shows them. */
export const REQUIRED: readonly (keyof DraftModel)[] = [
  "title",
  "question",
  "tables",
  "keywords",
  "reasoning_target",
  "sql_code",
  "result",
];

/** The three a submission can never supply, so the form can say so. */
export const JUDGEMENT: readonly (keyof DraftModel)[] = [
  "keywords",
  "reasoning_target",
  "result",
];

export const EMPTY: DraftModel = {
  title: "",
  question: "",
  tables: "",
  keywords: "",
  reasoning_target: "",
  sql_code: "",
  result: "",
  translation_note: "",
  suite: "",
  extra_meta: {},
};

/** Whatever the server sent, with every field present and a string. */
export function toDraft(value: Record<string, unknown> | null | undefined): DraftModel {
  const source = value ?? {};
  const draft: DraftModel = { ...EMPTY };
  for (const key of Object.keys(EMPTY) as (keyof DraftModel)[]) {
    if (key === "extra_meta") continue;
    const found = source[key];
    if (typeof found === "string") draft[key] = found as never;
  }
  const extra = source["extra_meta"];
  if (extra && typeof extra === "object" && !Array.isArray(extra)) {
    draft.extra_meta = Object.fromEntries(
      Object.entries(extra as Record<string, unknown>).map(([k, v]) => [k, String(v)]),
    );
  }
  return draft;
}

/** Required fields that are still empty. Empty list means "worth previewing". */
export function missing(draft: DraftModel): (keyof DraftModel)[] {
  return REQUIRED.filter((field) => !String(draft[field]).trim());
}
