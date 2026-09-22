/**
 * What the agent is doing, while it does it.
 *
 * The steps are the pipeline's own graph nodes, streamed as they finish, so
 * this is a report rather than an animation -- which is the point. A user
 * watching "matching literals" go by learns something about why the answer
 * took a minute, and a user watching a spinner learns that the page might
 * be broken.
 *
 * The full node list comes from `/v1/meta`, so the steps still to come are
 * shown greyed rather than appearing one at a time out of nowhere.
 */

import type { ProgressEvent } from "../api/types";

export interface ProgressProps {
  events: readonly ProgressEvent[];
  /** Every node this server runs, in order. Empty before `/v1/meta` lands. */
  nodes: readonly string[];
  done: boolean;
}

export function Progress({ events, nodes, done }: ProgressProps) {
  const seen = new Map(events.map((event) => [event.step, event]));
  // The node list is what the server said it runs; anything streamed that is
  // not in it is still shown, because the stream is the truth and the list
  // is only a prediction.
  const extra = events.filter((event) => !nodes.includes(event.step)).map((event) => event.step);
  const steps = [...nodes, ...new Set(extra)];
  const current = events[events.length - 1];

  return (
    <section className="progress" aria-label="Pipeline progress">
      <div className="progress-head">
        <span className="progress-spinner" aria-hidden="true" data-done={done} />
        <span className="progress-current" role="status">
          {done
            ? `Finished — ${events.length} steps`
            : current
              ? `${current.label}${current.detail ? ` — ${current.detail}` : ""}`
              : "Queued"}
        </span>
        {steps.length > 0 && (
          <span className="progress-count">
            {seen.size} / {steps.length}
          </span>
        )}
      </div>

      <ol className="progress-steps">
        {steps.map((step) => {
          const event = seen.get(step);
          return (
            <li
              key={step}
              className="progress-step"
              data-state={event ? "done" : "pending"}
              title={event?.detail ?? ""}
            >
              {event?.label ?? step}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
