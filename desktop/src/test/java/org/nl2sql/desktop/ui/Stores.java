package org.nl2sql.desktop.ui;

import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.feedback.FeedbackStore;
import org.nl2sql.desktop.feedback.Sender;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

/** A feedback store for the interface tests: no file, no threads, no server. */
final class Stores {

    /** Everything a verdict was sent to, so a test can assert on it. */
    static final class Recording implements Sender {
        boolean on = true;
        RuntimeException failure;
        final List<String> sent = new ArrayList<>();
        final List<String> withdrawn = new ArrayList<>();

        @Override
        public boolean enabled() {
            return on;
        }

        @Override
        public void submit(String jobId, Models.Verdict verdict, String comment) {
            sent.add(jobId + ":" + verdict.wire() + ":" + comment);
            if (failure != null) {
                throw failure;
            }
        }

        @Override
        public void withdraw(String jobId) {
            withdrawn.add(jobId);
            if (failure != null) {
                throw failure;
            }
        }
    }

    private Stores() {
    }

    static FeedbackStore of(Sender sender) {
        return new FeedbackStore(sender, null, Runnable::run,
                () -> Instant.parse("2026-09-25T10:00:00Z"), cause -> {
        });
    }

    static FeedbackStore of(Sender sender, List<String> problems) {
        return new FeedbackStore(sender, null, Runnable::run,
                () -> Instant.parse("2026-09-25T10:00:00Z"),
                cause -> problems.add(cause.getMessage()));
    }
}
