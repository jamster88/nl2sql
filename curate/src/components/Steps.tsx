/**
 * The loaders a write ran, and how each went.
 *
 * A write is the document first and the store second, and the second can
 * fail without undoing the first -- so the outcome is shown per step, in
 * the loader's own words, rather than as one success or failure.
 */

import type { StepModel } from "../api/types";

export interface StepsProps {
  steps: readonly StepModel[];
}

function state(step: StepModel): string {
  if (!step.ran) return "skipped";
  return step.ok ? "ok" : "FAILED";
}

export function Steps({ steps }: StepsProps) {
  if (steps.length === 0) return null;
  return (
    <ul className="steps">
      {steps.map((step) => (
        <li key={step.name} className={`step step-${state(step) === "FAILED" ? "failed" : state(step)}`}>
          <strong>{step.name}</strong> {state(step)}
          {step.detail && <span className="muted"> — {step.detail}</span>}
        </li>
      ))}
    </ul>
  );
}
