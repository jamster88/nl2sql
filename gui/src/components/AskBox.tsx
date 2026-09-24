/**
 * Where the question goes in.
 *
 * A textarea rather than an input because the questions that get good
 * answers are sentences, and a single-line box tells the user to type three
 * words. Enter submits and shift-Enter adds a line, which is what every
 * other message box does.
 */

import { useState, type FormEvent, type KeyboardEvent } from "react";

/**
 * Four questions from the benchmark suite, chosen to land on four different
 * chart shapes -- one number, a bar, a line and a grouped bar -- so the
 * first thing a new user clicks shows what the application can do rather
 * than the same table four times.
 */
export const EXAMPLES = [
  "How many stores are there?",
  "How many stores does each banner operate?",
  "What were our net sales by state in fiscal year 2025?",
  "Which 5 products had the highest net sales in fiscal year 2025? Give the SKU and the amount.",
] as const;

export interface AskBoxProps {
  onAsk: (question: string) => void;
  onCancel: () => void;
  busy: boolean;
  /** Hidden once the user has asked something; examples are for an empty page. */
  showExamples: boolean;
  maxLength?: number;
}

export function AskBox({ onAsk, onCancel, busy, showExamples, maxLength = 2000 }: AskBoxProps) {
  const [question, setQuestion] = useState("");

  function submit(event?: FormEvent): void {
    event?.preventDefault();
    if (busy || !question.trim()) return;
    onAsk(question);
    // Cleared on send, like every other message box. Leaving it would mean
    // the next thing typed is appended to the last question rather than
    // replacing it, and the question itself is not lost -- it heads the
    // answer and stays in the session list.
    setQuestion("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form className="ask" onSubmit={submit}>
      <label className="ask-label" htmlFor="question">
        Ask the retail database a question
      </label>
      <div className="ask-row">
        <textarea
          id="question"
          className="ask-input"
          rows={2}
          value={question}
          maxLength={maxLength}
          placeholder="What were our total net sales in fiscal year 2025?"
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
          disabled={busy}
        />
        {busy ? (
          <button type="button" className="button button-quiet" onClick={onCancel}>
            Stop
          </button>
        ) : (
          <button type="submit" className="button button-primary" disabled={!question.trim()}>
            Ask
          </button>
        )}
      </div>

      {showExamples && (
        <div className="ask-examples">
          <span className="ask-examples-label">Try</span>
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              className="chip"
              onClick={() => onAsk(example)}
            >
              {example}
            </button>
          ))}
        </div>
      )}
    </form>
  );
}
