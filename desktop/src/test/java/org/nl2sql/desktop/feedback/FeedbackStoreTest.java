package org.nl2sql.desktop.feedback;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.nl2sql.desktop.api.ApiException;
import org.nl2sql.desktop.api.Models;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.concurrent.Executor;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

class FeedbackStoreTest {

    /** A sender that records, and fails when told to. */
    private static final class Recording implements Sender {
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

    /** An executor that holds the work until a test lets it go. */
    private static final class Deferred implements Executor {
        private final Deque<Runnable> waiting = new ArrayDeque<>();

        @Override
        public void execute(Runnable command) {
            waiting.add(command);
        }

        void runAll() {
            while (!waiting.isEmpty()) {
                waiting.poll().run();
            }
        }
    }

    private Recording sender;
    private List<String> problems;
    private int announcements;

    @BeforeEach
    void setUp() {
        sender = new Recording();
        problems = new ArrayList<>();
        announcements = 0;
    }

    private FeedbackStore store(Path file, Executor executor) {
        FeedbackStore built = new FeedbackStore(sender, file, executor,
                () -> Instant.parse("2026-09-25T10:00:00Z"),
                cause -> problems.add(cause.getMessage()));
        built.subscribe(() -> announcements++);
        return built;
    }

    private FeedbackStore store() {
        return store(null, Runnable::run);
    }

    @Test
    void a_verdict_is_recorded_and_sent() {
        FeedbackStore store = store();

        store.vote("job-1", "how many stores", Models.Verdict.YES);

        assertEquals(List.of("job-1:yes:"), sender.sent);
        FeedbackRecord kept = store.get("job-1").orElseThrow();
        assertEquals(Models.Verdict.YES, kept.verdict());
        assertEquals(SyncState.SAVED, kept.sync());
        assertEquals("2026-09-25T10:00:00Z", kept.at());
    }

    @Test
    void the_click_is_recorded_before_the_send_rather_than_after_it() {
        // A verdict is a courtesy the user is doing us, and making them watch
        // a spinner for it is how a feedback system stops collecting feedback.
        Deferred later = new Deferred();
        FeedbackStore store = store(null, later);

        store.vote("job-1", "how many stores", Models.Verdict.NO);

        assertEquals(SyncState.SAVING, store.get("job-1").orElseThrow().sync());
        assertEquals(List.of(), sender.sent);
        later.runAll();
        assertEquals(SyncState.SAVED, store.get("job-1").orElseThrow().sync());
    }

    @Test
    void a_comment_amends_the_same_row_rather_than_adding_a_second_opinion() {
        FeedbackStore store = store();

        store.vote("job-1", "how many stores", Models.Verdict.NO);
        store.comment("job-1", "how many stores", Models.Verdict.NO, "off by one");

        assertEquals(List.of("job-1:no:", "job-1:no:off by one"), sender.sent);
        assertEquals(1, store.all().size());
    }

    @Test
    void a_send_that_fails_is_shown_as_unsent_and_can_be_retried() {
        // The one case where saying nothing actively misleads: the user
        // believes they have reported the problem and nobody has heard it.
        FeedbackStore store = store();
        sender.failure = new ApiException(0, "unreachable", "cannot reach the API");

        store.comment("job-1", "how many stores", Models.Verdict.NO, "off by one");

        FeedbackRecord kept = store.get("job-1").orElseThrow();
        assertEquals(SyncState.FAILED, kept.sync());
        assertEquals("cannot reach the API", kept.error());
        assertEquals(List.of("cannot reach the API"), problems);

        sender.failure = null;
        store.retry("job-1");

        assertEquals(SyncState.SAVED, store.get("job-1").orElseThrow().sync());
        // The comment goes with it; a retry that dropped it would send a
        // different verdict from the one that failed.
        assertEquals(List.of("job-1:no:off by one", "job-1:no:off by one"), sender.sent);
    }

    @Test
    void a_failure_with_no_message_still_says_something() {
        FeedbackStore store = store();
        sender.failure = new IllegalStateException();

        store.vote("job-1", "q", Models.Verdict.YES);

        assertEquals("IllegalStateException", store.get("job-1").orElseThrow().error());
    }

    @Test
    void retrying_something_that_did_not_fail_does_nothing() {
        FeedbackStore store = store();
        store.vote("job-1", "q", Models.Verdict.YES);

        store.retry("job-1");
        store.retry("never-voted-on");

        assertEquals(1, sender.sent.size());
    }

    @Test
    void a_verdict_replaced_while_the_first_was_in_flight_is_not_overwritten_by_it() {
        // A user who changed their mind has a newer verdict on screen, and
        // showing the older one's outcome would replace an answer they had
        // already replaced.
        Deferred later = new Deferred();
        FeedbackStore store = store(null, later);

        store.vote("job-1", "q", Models.Verdict.YES);
        store.vote("job-1", "q", Models.Verdict.NO);
        later.runAll();

        FeedbackRecord kept = store.get("job-1").orElseThrow();
        assertEquals(Models.Verdict.NO, kept.verdict());
        assertEquals(SyncState.SAVED, kept.sync());
    }

    @Test
    void withdrawing_removes_it_here_and_there() {
        FeedbackStore store = store();
        store.vote("job-1", "q", Models.Verdict.NO);

        store.withdraw("job-1");

        assertTrue(store.get("job-1").isEmpty());
        assertEquals(List.of("job-1"), sender.withdrawn);
    }

    @Test
    void a_withdrawal_that_does_not_reach_the_server_is_reported_but_still_withdrawn_here() {
        // Re-showing a verdict somebody just withdrew would be the more
        // confusing lie.
        FeedbackStore store = store();
        store.vote("job-1", "q", Models.Verdict.NO);
        sender.failure = new ApiException(0, "unreachable", "cannot reach the API");

        store.withdraw("job-1");

        assertTrue(store.get("job-1").isEmpty());
        assertEquals(List.of("cannot reach the API"), problems);
    }

    @Test
    void withdrawing_something_that_was_never_voted_on_sends_nothing() {
        store().withdraw("job-1");

        assertEquals(List.of(), sender.withdrawn);
    }

    @Test
    void a_server_that_takes_no_feedback_records_the_verdict_and_says_so() {
        // Not `failed`, which would put "not sent" under every answer on a
        // server that was never going to take one.
        sender.on = false;
        FeedbackStore store = store();

        store.vote("job-1", "q", Models.Verdict.YES);
        store.withdraw("job-1");

        assertFalse(store.sends());
        assertEquals(List.of(), sender.sent);
        assertEquals(List.of(), sender.withdrawn);
    }

    @Test
    void the_newest_verdict_is_first_and_the_list_is_bounded() {
        FeedbackStore store = store();

        for (int index = 0; index < FeedbackStore.MAX_RECORDS + 5; index++) {
            store.vote("job-" + index, "q" + index, Models.Verdict.YES);
        }

        assertEquals(FeedbackStore.MAX_RECORDS, store.all().size());
        assertEquals("job-" + (FeedbackStore.MAX_RECORDS + 4), store.all().get(0).jobId());
    }

    @Test
    void listeners_are_told_and_can_stop_being_told() {
        FeedbackStore store = store();
        int[] second = {0};
        Runnable unsubscribe = store.subscribe(() -> second[0]++);

        store.vote("job-1", "q", Models.Verdict.YES);
        unsubscribe.run();
        store.vote("job-2", "q", Models.Verdict.YES);

        assertTrue(announcements > second[0]);
        assertEquals(2, second[0]);
    }

    @Test
    void a_verdict_outlives_a_restart(@TempDir Path scratch) {
        Path file = scratch.resolve("nested").resolve("feedback.json");
        FeedbackStore first = store(file, Runnable::run);
        sender.failure = new ApiException(0, "unreachable", "cannot reach the API");
        first.vote("job-1", "how many stores", Models.Verdict.NO);

        FeedbackStore second = store(file, Runnable::run);

        FeedbackRecord kept = second.get("job-1").orElseThrow();
        assertEquals(Models.Verdict.NO, kept.verdict());
        // Still marked unsent, because it still is.
        assertEquals(SyncState.FAILED, kept.sync());
        assertEquals("how many stores", kept.question());
    }

    @Test
    void a_file_from_a_version_this_one_does_not_understand_is_reported_and_stepped_over(
            @TempDir Path scratch) throws IOException {
        Path file = scratch.resolve("feedback.json");
        Files.writeString(file, "{ this is not json");

        FeedbackStore store = store(file, Runnable::run);

        assertEquals(List.of(), store.all());
        assertEquals(1, problems.size());
        assertTrue(problems.get(0).contains("could not read the verdicts"));
    }

    @Test
    void a_file_with_no_records_in_it_is_an_empty_store(@TempDir Path scratch) throws IOException {
        Path file = scratch.resolve("feedback.json");
        Files.writeString(file, "{\"version\": 1}");

        assertEquals(List.of(), store(file, Runnable::run).all());
        assertEquals(List.of(), problems);
    }

    @Test
    void somewhere_unwritable_costs_the_file_and_not_the_session(@TempDir Path scratch)
            throws IOException {
        // Out of space, or a directory that is not writable. The verdict
        // still shows; it just will not outlive the session.
        Path blocked = scratch.resolve("blocked");
        Files.writeString(blocked, "not a directory");
        FeedbackStore store = store(blocked.resolve("feedback.json"), Runnable::run);

        store.vote("job-1", "q", Models.Verdict.YES);

        assertEquals(Models.Verdict.YES, store.get("job-1").orElseThrow().verdict());
        assertTrue(problems.stream().anyMatch(said -> said.contains("could not keep the verdict")));
    }

    @Test
    void a_record_carries_a_state_even_when_it_was_made_without_one() {
        FeedbackRecord bare = new FeedbackRecord("job-1", "q", Models.Verdict.YES,
                "2026-09-25T10:00:00Z", null, null);

        assertSame(SyncState.LOCAL, bare.sync());
        assertEquals("", bare.error());
        assertEquals(SyncState.SAVED, bare.in(SyncState.SAVED, "").sync());
    }

    @Test
    void every_state_has_something_to_say_beside_the_verdict() {
        for (SyncState state : SyncState.values()) {
            assertFalse(state.note().isBlank(), state + " has no note");
        }
    }


    @Test
    void a_verdict_withdrawn_while_it_was_in_flight_does_not_come_back() {
        Deferred later = new Deferred();
        FeedbackStore store = store(null, later);
        store.vote("job-1", "q", Models.Verdict.YES);
        store.withdraw("job-1");

        later.runAll();

        assertTrue(store.get("job-1").isEmpty());
    }

    @Test
    void a_file_in_the_working_directory_needs_no_directory_made_for_it(@TempDir Path scratch)
            throws IOException {
        Path here = Path.of("nl2sql-desktop-test-feedback.json");
        try {
            FeedbackStore store = store(here, Runnable::run);

            store.vote("job-1", "q", Models.Verdict.YES);

            assertTrue(Files.exists(here));
            assertEquals(List.of(), problems);
        } finally {
            Files.deleteIfExists(here);
        }
    }


    @Test
    void withdrawing_one_verdict_leaves_the_others_alone() {
        FeedbackStore store = store();
        store.vote("job-1", "first", Models.Verdict.YES);
        store.vote("job-2", "second", Models.Verdict.NO);

        store.withdraw("job-2");

        assertTrue(store.get("job-1").isPresent());
        assertTrue(store.get("job-2").isEmpty());
    }

    @Test
    void a_failure_whose_message_is_only_spaces_is_named_by_its_kind() {
        FeedbackStore store = store();
        sender.failure = new IllegalStateException("   ");

        store.vote("job-1", "q", Models.Verdict.YES);

        assertEquals("IllegalStateException", store.get("job-1").orElseThrow().error());
    }
}
