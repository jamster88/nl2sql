package org.nl2sql.desktop.feedback;

/**
 * Where a verdict has got to.
 *
 * <p>{@link #LOCAL} is the honest answer on a server with no staging
 * database: it was recorded here and was never going anywhere else. The other
 * three exist so the interface can say "your click was heard but the server
 * has not confirmed it" instead of looking identical to success. A verdict
 * that silently failed to send is worse than one that was never offered --
 * the user believes they have reported the problem, and nobody has heard it.
 */
public enum SyncState {
    LOCAL("click again to withdraw"),
    SAVING("sending…"),
    SAVED("sent for review"),
    FAILED("not sent");

    private final String note;

    SyncState(String note) {
        this.note = note;
    }

    /** What to show beside the verdict. */
    public String note() {
        return note;
    }
}
