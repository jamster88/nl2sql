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
 *
 * Under the ensemble (7.0) the question is asked several ways and each
 * wording is a run of the whole pipeline, so one row of steps would be four
 * runs written over each other. Every event says which run it came from
 * (`candidate`), and the panel is drawn from that: a lane per run, each with
 * the pipeline's steps, and the ensemble's own steps above and below them --
 * what it did before the runs, and what it did with their answers. On a host
 * that serves one model call at a time the lanes fill one after another, and
 * side by side when the deployer has allowed more; either way it is the
 * order things happened in.
 */

import type { EnsembleSettings, ProgressEvent } from "../api/types";

export interface ProgressProps {
  events: readonly ProgressEvent[];
  /** Every node this server runs, in order. Empty before `/v1/meta` lands. */
  nodes: readonly string[];
  done: boolean;
  /** From `/v1/meta`: how many runs to expect, and which nodes are the ensemble's own. */
  ensemble?: EnsembleSettings | null;
}

export function Progress({ events, nodes, done, ensemble = null }: ProgressProps) {
  // The stream is the truth: an event naming a run is drawn in that run's
  // lane whether or not `/v1/meta` has said yet that there are runs.
  const laned = (ensemble?.enabled ?? false) || events.some((event) => isRun(event));
  return laned ? (
    <Lanes events={events} nodes={nodes} done={done} ensemble={ensemble} />
  ) : (
    <Single events={events} nodes={nodes} done={done} />
  );
}

/** A step of one of the runs, rather than one of the ensemble's own. */
function isRun(event: ProgressEvent): event is ProgressEvent & { candidate: number } {
  return event.candidate !== null && event.candidate !== undefined;
}

/** The run's name in the panel: the question as it was typed, or a rewording of it. */
export function runName(candidate: number): string {
  return candidate === 0 ? "As asked" : `Rewording ${candidate}`;
}

function described(event: ProgressEvent): string {
  return `${event.label}${event.detail ? ` — ${event.detail}` : ""}`;
}

/**
 * The listed steps, then any streamed step the list did not name: the node
 * list is what the server said it runs, and the stream is what it ran.
 */
function withExtras(listed: readonly string[], events: readonly ProgressEvent[]): string[] {
  const extra = events.filter((event) => !listed.includes(event.step)).map((event) => event.step);
  return [...new Set([...listed, ...extra])];
}

interface StepsProps {
  steps: readonly string[];
  events: readonly ProgressEvent[];
  label?: string;
}

function Steps({ steps, events, label }: StepsProps) {
  const seen = new Map(events.map((event) => [event.step, event]));
  return (
    <ol className="progress-steps" aria-label={label}>
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
  );
}

/** One run of the pipeline: the server before 7.0, or one with the ensemble off. */
function Single({ events, nodes, done }: Omit<ProgressProps, "ensemble">) {
  const steps = withExtras(nodes, events);
  const seen = new Set(events.map((event) => event.step));
  const current = events[events.length - 1];

  return (
    <section className="progress" aria-label="Pipeline progress">
      <div className="progress-head">
        <span className="progress-spinner" aria-hidden="true" data-done={done} />
        <span className="progress-current" role="status">
          {done ? `Finished — ${events.length} steps` : current ? described(current) : "Queued"}
        </span>
        {steps.length > 0 && (
          <span className="progress-count">
            {seen.size} / {steps.length}
          </span>
        )}
      </div>

      <Steps steps={steps} events={events} />
    </section>
  );
}

function Lanes({ events, nodes, done, ensemble }: Required<ProgressProps>) {
  const own = events.filter((event) => !isRun(event));
  const runs = events.filter(isRun);
  const first = runs[0];

  // `nodes` is the ensemble's own and then each run's; the ensemble's are
  // named again by themselves, which is where the list divides.
  const ownNodes = ensemble?.nodes ?? [];
  const outer = withExtras(ownNodes, own);
  const inner = nodes.slice(ownNodes.length);

  // Above the lanes, what was done before any run began; below, the rest.
  // Until a run reports there is no telling which is which, so they are all
  // above and the lanes wait beneath them. A step that never ran (a question
  // that was not refused) stays with its neighbours rather than dropping
  // below: the division is by place in the list, not by what has finished.
  const last = first
    ? Math.max(-1, ...own.filter((event) => event.seq < first.seq).map((event) => outer.indexOf(event.step)))
    : outer.length - 1;
  const before = outer.slice(0, last + 1);
  const after = outer.slice(last + 1);

  // A lane for every run that has reported, in the order they began, and a
  // waiting lane for each one still expected -- until the ensemble moves on
  // to the answers, when the runs that will happen have happened.
  const started = [...new Set(runs.map((event) => event.candidate))];
  const expected = ensemble?.enabled ? 1 + ensemble.paraphrases : 0;
  const moved = first !== undefined && own.some((event) => event.seq > first.seq);
  const waiting = moved || done ? 0 : Math.max(0, expected - started.length);
  const current = events[events.length - 1];
  // A question refused before any run finished in no runs, and says so by
  // not mentioning them.
  const inRuns = started.length === 0 ? "" : ` in ${started.length} ${started.length === 1 ? "run" : "runs"}`;

  return (
    <section className="progress" aria-label="Pipeline progress">
      <div className="progress-head">
        <span className="progress-spinner" aria-hidden="true" data-done={done} />
        <span className="progress-current" role="status">
          {done
            ? `Finished — ${events.length} steps${inRuns}`
            : current
              ? `${isRun(current) ? `${runName(current.candidate)}: ` : ""}${described(current)}`
              : "Queued"}
        </span>
        {started.length + waiting > 0 && (
          <span className="progress-count">
            {started.length} / {started.length + waiting} runs
          </span>
        )}
      </div>

      {before.length > 0 && <Steps steps={before} events={own} label="Before the runs" />}

      {started.length + waiting > 0 && (
        <ol className="progress-lanes" aria-label="The runs">
          {started.map((candidate) => {
            const mine = runs.filter((event) => event.candidate === candidate);
            const latest = mine[mine.length - 1] as ProgressEvent;
            const active = !done && current === latest;
            return (
              <li
                key={candidate}
                className="progress-lane"
                data-state={active ? "active" : "reported"}
                aria-label={runName(candidate)}
              >
                <div className="progress-lane-head">
                  <span className="progress-lane-name">{runName(candidate)}</span>
                  <span className="progress-lane-current">{described(latest)}</span>
                </div>
                <Steps steps={withExtras(inner, mine)} events={mine} />
              </li>
            );
          })}
          {Array.from({ length: waiting }, (_, index) => (
            <li key={`waiting-${index}`} className="progress-lane" data-state="waiting">
              <div className="progress-lane-head">
                <span className="progress-lane-name">Run {started.length + index + 1}</span>
                <span className="progress-lane-current">waiting</span>
              </div>
              <Steps steps={inner} events={[]} />
            </li>
          ))}
        </ol>
      )}

      {after.length > 0 && <Steps steps={after} events={own} label="After the runs" />}
    </section>
  );
}
