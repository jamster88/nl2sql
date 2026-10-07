package org.nl2sql.desktop.api;

/**
 * Who this client is, as far as the servers are concerned.
 *
 * <p>Two ways to be somebody. A {@code --token} is a service token, handed to
 * a script by whoever runs the deployment; signing in is a person, and the
 * server runs their questions as their own database role. A session, once
 * there is one, wins: the person at the keyboard is the more specific answer,
 * and a service token left in a shell's environment should not quietly
 * outrank them.
 *
 * <p>Held in memory and nowhere else. A session token on disk is a password
 * that does not need typing, readable by anything running as the same user,
 * and the cost of not keeping one is signing in again when the window is
 * reopened.
 *
 * <p>Read from the worker threads and written from the interface's, so the
 * one field is volatile: a question asked a moment after signing in must
 * carry the new session, not the empty one the pool's thread last saw.
 */
public final class Session {

    private final String fallback;
    private volatile Models.Token current;

    /** @param fallback the static token from the settings, or empty for none */
    public Session(String fallback) {
        this.fallback = fallback;
    }

    /** The token to present: the session's, else the static one, else empty. */
    public String bearer() {
        Models.Token held = current;
        return held == null ? fallback : held.token();
    }

    /** The session, or null when nobody has signed in. */
    public Models.Token current() {
        return current;
    }

    public void signedIn(Models.Token token) {
        current = token;
    }

    public void signOut() {
        current = null;
    }
}
