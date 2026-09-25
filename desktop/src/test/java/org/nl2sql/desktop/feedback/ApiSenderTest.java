package org.nl2sql.desktop.feedback;

import org.junit.jupiter.api.Test;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.api.ApiException;
import org.nl2sql.desktop.api.Models;

import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ApiSenderTest {

    @Test
    void a_verdict_takes_the_same_route_as_one_given_in_the_web_interface() {
        // The same endpoint, the same staging table, the same review queue.
        // The reviewer sees one queue because there is only one.
        Fakes.FakeClient client = new Fakes.FakeClient();
        Sender sender = new ApiSender(client, () -> true);

        sender.submit("job-1", Models.Verdict.NO, "off by one");
        sender.withdraw("job-1");

        assertEquals(List.of("job-1:no:off by one"), client.votes);
        assertEquals(List.of("job-1"), client.withdrawn);
    }

    @Test
    void whether_the_server_takes_feedback_is_read_fresh_on_every_vote() {
        // `/v1/meta` answers after the store is built, and a sender that
        // captured the value at construction would decide "no" before it had
        // been told.
        AtomicBoolean accepted = new AtomicBoolean(false);
        Sender sender = new ApiSender(new Fakes.FakeClient(), accepted::get);

        assertFalse(sender.enabled());
        accepted.set(true);
        assertTrue(sender.enabled());
    }

    @Test
    void a_failure_reaches_the_caller_rather_than_being_swallowed() {
        Fakes.FakeClient client = new Fakes.FakeClient();
        client.failFeedback = new ApiException(409, "already_reviewed", "somebody has acted on it");
        Sender sender = new ApiSender(client, () -> true);

        assertEquals("already_reviewed", assertThrows(ApiException.class,
                () -> sender.submit("job-1", Models.Verdict.YES, "")).code());
        assertEquals("already_reviewed", assertThrows(ApiException.class,
                () -> sender.withdraw("job-1")).code());
    }

    @Test
    void the_sender_for_a_server_with_no_staging_database_refuses_to_pretend() {
        // It is never asked to send -- the store checks `enabled()` first --
        // and if it ever were, quietly doing nothing would be worse.
        Sender none = Sender.none();

        assertFalse(none.enabled());
        assertThrows(IllegalStateException.class, () -> none.submit("job-1", Models.Verdict.YES, ""));
        assertThrows(IllegalStateException.class, () -> none.withdraw("job-1"));
    }
}
