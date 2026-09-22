/**
 * A panel that starts closed.
 *
 * The SQL, the trace and the matched literals are the reason to trust the
 * answer, and they are also four hundred characters of monospace that the
 * person who just wanted a number does not want to scroll past. A native
 * `<details>` gets keyboard behaviour, screen-reader semantics and
 * find-in-page expansion without a line of script.
 */

import type { ReactNode } from "react";

export interface DisclosureProps {
  summary: string;
  /** A count or a duration shown next to the title, closed or open. */
  badge?: string | undefined;
  open?: boolean;
  children: ReactNode;
}

export function Disclosure({ summary, badge, open = false, children }: DisclosureProps) {
  return (
    <details className="disclosure" open={open}>
      <summary>
        {summary}
        {badge !== undefined && <span className="disclosure-badge">{badge}</span>}
      </summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}
