package org.nl2sql.desktop.feedback;

import org.nl2sql.desktop.api.Models;

/**
 * Where a verdict goes after it has been recorded here.
 *
 * <p>Separated from the store so that the store -- which owns what is on
 * screen, what is on disk and what the user is told -- can be tested without a
 * server, and so the one place that knows a network is involved is the one
 * place that has to handle it failing.
 */
public interface Sender {

    /** True when this server will take a verdict at all. Read fresh on every vote. */
    boolean enabled();

    /** Record a verdict. Throws when it does not reach the server. */
    void submit(String jobId, Models.Verdict verdict, String comment);

    /** Take one back. Throws on the same terms. */
    void withdraw(String jobId);

    /**
     * A sender for a server that has not said it takes feedback yet, or at all.
     *
     * <p>Not a sender that fails: a verdict given to it is recorded and marked
     * {@code local}, which is what it is. Marking it {@code failed} would put
     * "not sent" under every answer on a server that was never going to take
     * one, and that is not a failure of anything.
     */
    static Sender none() {
        return new Sender() {
            @Override
            public boolean enabled() {
                return false;
            }

            @Override
            public void submit(String jobId, Models.Verdict verdict, String comment) {
                throw new IllegalStateException("this sender was asked to send with feedback off");
            }

            @Override
            public void withdraw(String jobId) {
                throw new IllegalStateException("this sender was asked to send with feedback off");
            }
        };
    }
}
