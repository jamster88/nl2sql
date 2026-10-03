/**
 * Removing something, in two steps.
 *
 * Everything this page removes leaves a document or a store, so the first
 * click says what will happen -- in the words of the file and the store it
 * leaves -- and the second does it. No browser dialog: an in-page question
 * can say more, and cannot be dismissed by a stray Enter.
 */

import { useState } from "react";

export interface RemoveButtonProps {
  /** What is being removed, as the confirming button names it: `S11`. */
  what: string;
  /** What removing it does, said before it is done. */
  consequence: string;
  busy: boolean;
  onConfirm: () => void;
}

export function RemoveButton({ what, consequence, busy, onConfirm }: RemoveButtonProps) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <button type="button" className="button button-danger" disabled={busy} onClick={() => setAsking(true)}>
        Remove…
      </button>
    );
  }
  return (
    <div className="confirm" role="alertdialog" aria-label="Are you sure?">
      <p>
        <strong>{consequence}</strong>
      </p>
      <div className="actions">
        <button
          type="button"
          className="button button-danger"
          disabled={busy}
          onClick={() => {
            setAsking(false);
            onConfirm();
          }}
        >
          Remove {what}
        </button>
        <button type="button" className="button button-quiet" onClick={() => setAsking(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}
