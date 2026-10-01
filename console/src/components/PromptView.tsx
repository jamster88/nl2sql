/**
 * What the agent's prompt says about one table.
 *
 * The exact block the agent builds -- `Database.schema_and_samples`, run
 * here as it runs there -- with its description, columns, keys and sample
 * rows. When the agent picked the wrong column, this is usually why: the
 * model only ever knew what this text told it.
 */

import type { PromptModel } from "../api/types";

export interface PromptViewProps {
  prompt: PromptModel;
}

export function PromptView({ prompt }: PromptViewProps) {
  return (
    <section className="prompt" aria-label="Agent's view">
      <p className="small">
        The block of the agent's prompt for <span className="mono">{prompt.table}</span>, with the{" "}
        {prompt.sample_rows} sample rows it carries (SAMPLE_ROWS).
      </p>
      <pre className="code">{prompt.text}</pre>
    </section>
  );
}
