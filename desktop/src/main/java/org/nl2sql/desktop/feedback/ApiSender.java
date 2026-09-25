package org.nl2sql.desktop.feedback;

import org.nl2sql.desktop.api.ApiClient;
import org.nl2sql.desktop.api.Models;

import java.util.function.BooleanSupplier;

/**
 * The sender that reaches the agent's API, which is the whole of the
 * integration with the feedback system.
 *
 * <p>There is nothing else to it, and that is the point: a verdict from this
 * application takes the same route as a verdict from the web interface --
 * {@code POST /v1/questions/&#123;id&#125;/feedback}, the same staging table, the same
 * row-level security fence, the same review queue. The reviewer sees one
 * queue because there is only one, not because two writers were made to agree.
 */
public final class ApiSender implements Sender {

    private final ApiClient client;
    private final BooleanSupplier accepted;

    /**
     * @param accepted whether {@code /v1/meta} said this server takes feedback.
     *                 A supplier rather than a boolean because meta answers
     *                 after the store is built, and a sender that captured the
     *                 value at construction would decide "no" before it had
     *                 been told.
     */
    public ApiSender(ApiClient client, BooleanSupplier accepted) {
        this.client = client;
        this.accepted = accepted;
    }

    @Override
    public boolean enabled() {
        return accepted.getAsBoolean();
    }

    @Override
    public void submit(String jobId, Models.Verdict verdict, String comment) {
        client.submitFeedback(jobId, verdict, comment);
    }

    @Override
    public void withdraw(String jobId) {
        client.withdrawFeedback(jobId);
    }
}
