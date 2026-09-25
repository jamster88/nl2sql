package org.nl2sql.desktop.api;

import java.io.Serial;

/**
 * A failure the server described, or a failure to reach it at all.
 *
 * <p>{@code code} is the stable, machine-readable half of the API's error
 * envelope and is what to branch on; {@code message} is written for a person
 * and may be reworded between releases. A network-level failure has no
 * envelope to read, so it is given the same shape -- status {@code 0} and the
 * code {@code unreachable} -- because the interface shows every failure the
 * same way and code that has to unwrap two shapes eventually unwraps one.
 */
public final class ApiException extends RuntimeException {

    @Serial
    private static final long serialVersionUID = 1L;

    private final int status;
    private final String code;

    public ApiException(int status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    public int status() {
        return status;
    }

    public String code() {
        return code;
    }

    /** True when retrying the same request might work. */
    public boolean retryable() {
        return status == 503 || status == 0;
    }
}
