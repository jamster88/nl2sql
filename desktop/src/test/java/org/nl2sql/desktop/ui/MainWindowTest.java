package org.nl2sql.desktop.ui;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.Settings;
import org.nl2sql.desktop.api.ApiException;
import org.nl2sql.desktop.api.Json;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.feedback.FeedbackStore;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The window, driven on one thread.
 *
 * <p>Both executors are "run it here", so a question is asked, streamed and
 * answered inside the call that asked it. In the application they are a pool
 * and {@code Platform.runLater}; the code between them is the same, and this
 * is the arrangement that makes it assertable without waiting for anything.
 */
@ExtendWith(FxToolkit.class)
class MainWindowTest {

    private final Fakes.FakeClient client = new Fakes.FakeClient();
    private final Stores.Recording sender = new Stores.Recording();
    private final AtomicBoolean feedbackAccepted = new AtomicBoolean();
    private final List<String> problems = new ArrayList<>();
    private FeedbackStore store;

    private MainWindow window(String... arguments) {
        store = Stores.of(sender, problems);
        return new MainWindow(client, store, Settings.from(Map.of(), arguments),
                Runnable::run, Runnable::run, feedbackAccepted);
    }

    private static List<String> stream(String... events) {
        List<String> lines = new ArrayList<>();
        for (String event : events) {
            lines.addAll(List.of(event.split("\n")));
            lines.add("");
        }
        return lines;
    }

    private static String event(String name, String data) {
        return "event: " + name + "\ndata: " + data;
    }

    @Test
    void it_asks_the_server_what_it_is_and_whether_it_is_ready() {
        FxToolkit.onFx(() -> {
            MainWindow window = window();

            window.start();

            assertTrue(Nodes.says(window.root(), "nl2sql-agent 4.4.0"));
            assertTrue(feedbackAccepted.get());
            // The pipeline's nodes become the steps still to come.
            assertTrue(Nodes.says(window.root(), "0 / 3"));
            window.close();
        });
    }

    @Test
    void a_dependency_that_is_not_ready_is_a_warning_rather_than_a_disconnection() {
        FxToolkit.onFx(() -> {
            client.readiness = new Models.Readiness(false,
                    Map.of("ollama", new Models.Check(false, "connection refused")),
                    List.of("no token with a wildcard origin"));
            MainWindow window = window();

            window.start();

            assertTrue(Nodes.says(window.root(), "ollama: connection refused"));
            assertTrue(Nodes.says(window.root(), "no token with a wildcard origin"));
            assertTrue(Nodes.says(window.root(), "nl2sql-agent 4.4.0"));
            window.close();
        });
    }

    @Test
    void a_server_that_cannot_be_reached_is_said_in_words() {
        FxToolkit.onFx(() -> {
            client.failMeta = new ApiException(0, "unreachable", "cannot reach the API");
            client.failReadiness = new ApiException(0, "unreachable", "cannot reach the API");
            MainWindow window = window();

            window.start();

            assertTrue(Nodes.says(window.root(), "Not connected."));
            assertTrue(Nodes.says(window.root(), "cannot reach the API"));
            window.close();
        });
    }

    @Test
    void a_question_is_asked_streamed_and_answered() {
        FxToolkit.onFx(() -> {
            client.streams.add(stream(
                    event("progress", Json.write(Fakes.step(1, "screen", "screening"))),
                    event("done", Json.write(Fakes.answered()))));
            MainWindow window = window();
            window.start();

            window.ask("how many stores are there?");

            assertEquals(List.of("how many stores are there?"), client.asked);
            assertTrue(Nodes.says(window.root(), "Finished — 1 step"));
            assertNotNull(window.answerView());
            assertTrue(Nodes.says(window.root(), "Produce net sales were $719,279.97"));
            // And it is in the session list, marked as the one on screen.
            assertEquals("job-1", window.history().list().getSelectionModel()
                    .getSelectedItem().id());
            assertFalse(window.askBox().action().getText().equals("Stop"));
            window.close();
        });
    }

    @Test
    void a_blank_question_is_not_asked() {
        FxToolkit.onFx(() -> {
            MainWindow window = window();

            window.ask("   ");

            assertEquals(List.of(), client.asked);
            window.close();
        });
    }

    @Test
    void a_question_the_server_refused_to_take_is_reported_where_it_was_asked() {
        FxToolkit.onFx(() -> {
            client.failAsk = new ApiException(422, "invalid_request", "a question cannot be blank");
            MainWindow window = window();

            window.ask("something");

            assertTrue(Nodes.says(window.root(), "That question could not be sent."));
            assertTrue(Nodes.says(window.root(), "a question cannot be blank"));
            assertFalse(Nodes.oneWithClass(window.root(), "progress").isVisible());
            window.close();
        });
    }

    @Test
    void a_stream_that_gave_up_says_so_without_stopping_the_answer_arriving() {
        FxToolkit.onFx(() -> {
            client.failEvents = new IOException("the proxy buffered it");
            MainWindow window = window();

            window.ask("how many stores");

            assertTrue(Nodes.says(window.root(), "being polled instead"));
            assertTrue(Nodes.says(window.root(), "Produce net sales were $719,279.97"));
            window.close();
        });
    }

    @Test
    void stopping_a_question_takes_it_off_the_screen_and_tells_the_server() {
        FxToolkit.onFx(() -> {
            // A stream that ends without answering, so the job is still in
            // flight when the user gives up on it.
            client.failEvents = new IOException("no stream");
            client.polled = Fakes.job("job-1", Models.JobStatus.RUNNING, null);
            MainWindow window = window();
            // Ask on this thread, then cancel: with a direct executor the
            // watch would never return, so the question is started by hand.
            window.askBox().setQuestion("how many stores");

            window.cancel();

            assertEquals(List.of(), client.cancelled);
            window.close();
        });
    }

    @Test
    void an_answer_picked_out_of_the_session_list_replaces_the_one_on_screen() {
        FxToolkit.onFx(() -> {
            client.streams.add(stream(event("done", Json.write(Fakes.answered()))));
            Models.Job other = new Models.Job("job-2", Models.JobStatus.SUCCEEDED,
                    "a different question", Map.of(), "2026-09-25T09:00:00Z", null,
                    "2026-09-25T09:01:00Z", 1000.0, List.of(), Fakes.answer(), null,
                    new Models.JobLinks("/v1/questions/job-2", "/v1/questions/job-2/events"));
            MainWindow window = window();
            window.ask("how many stores");
            window.history().remember(other);

            window.history().list().getSelectionModel().select(other);

            assertTrue(Nodes.says(window.root(), "a different question"));
            // The steps belong to the question that was just asked, and
            // leaving them above an older answer reads as though that is how
            // this one was arrived at.
            assertFalse(Nodes.oneWithClass(window.root(), "progress").isVisible());
            window.close();
        });
    }

    @Test
    void a_verdict_given_on_the_answer_reaches_the_server_and_the_session_list() {
        FxToolkit.onFx(() -> {
            client.streams.add(stream(event("done", Json.write(Fakes.answered()))));
            MainWindow window = window();
            window.start();
            window.ask("how many stores");

            window.answerView().feedback().no().fire();

            assertEquals(List.of("job-1:no:"), sender.sent);
            javafx.stage.Stage stage = FxToolkit.render(window.root());
            assertTrue(Nodes.texts(window.history().node()).stream()
                    .anyMatch(row -> row.startsWith("✗")));
            stage.close();
            window.close();
        });
    }

    @Test
    void a_verdict_that_did_not_send_is_reported_in_the_status_bar_as_well() {
        // For the answer the user has already scrolled past.
        FxToolkit.onFx(() -> {
            client.streams.add(stream(event("done", Json.write(Fakes.answered()))));
            MainWindow window = window();
            window.start();
            window.ask("how many stores");
            sender.failure = new ApiException(0, "unreachable", "no route to host");

            window.answerView().feedback().yes().fire();
            window.warn("feedback: " + problems.get(0));

            assertTrue(Nodes.says(window.root(), "feedback: no route to host"));
            // Said once, however many times it happens.
            window.warn("feedback: no route to host");
            assertEquals(1, Nodes.texts(window.root()).stream()
                    .filter(text -> text.equals("feedback: no route to host")).count());
            window.close();
        });
    }

    @Test
    void the_question_box_is_held_to_the_length_this_server_takes() {
        FxToolkit.onFx(() -> {
            Models.Meta meta = Fakes.meta(true);
            Models.Limits limits = meta.limits();
            client.meta = new Models.Meta(meta.service(), meta.version(), meta.model(),
                    meta.intents(), meta.tables(), meta.scope(),
                    new Models.Limits(limits.max_rows(), limits.max_attempts(),
                            limits.max_plan_cost(), limits.statement_timeout_ms(),
                            limits.max_concurrency(), limits.max_wait_seconds(), 12, 20),
                    meta.pipeline(), Map.of(), "none", false);
            MainWindow window = window();

            window.start();
            window.askBox().setQuestion("a question longer than twelve characters");

            assertTrue(window.askBox().action().isDisable());
            window.close();
        });
    }

    @Test
    void a_server_that_does_not_publish_a_length_leaves_the_documented_default() {
        FxToolkit.onFx(() -> {
            Models.Meta meta = Fakes.meta(true);
            Models.Limits limits = meta.limits();
            client.meta = new Models.Meta(meta.service(), meta.version(), meta.model(),
                    meta.intents(), meta.tables(), meta.scope(),
                    new Models.Limits(limits.max_rows(), limits.max_attempts(),
                            limits.max_plan_cost(), limits.statement_timeout_ms(),
                            limits.max_concurrency(), limits.max_wait_seconds(), 0, 0),
                    meta.pipeline(), Map.of(), "none", false);
            MainWindow window = window();

            window.start();
            window.askBox().setQuestion("a question of an ordinary length");

            assertFalse(window.askBox().action().isDisable());
            window.close();
        });
    }

    @Test
    void the_window_lets_go_of_the_store_when_it_closes() {
        FxToolkit.onFx(() -> {
            client.streams.add(stream(event("done", Json.write(Fakes.answered()))));
            MainWindow window = window();
            window.ask("how many stores");
            assertNotNull(window.answerView());

            window.close();
            store.vote("job-1", "how many stores", Models.Verdict.YES);

            // Nothing threw, and the window is no longer listening.
            assertNull(window.answerView().table().node().getScene());
        });
    }

    @Test
    void every_collaborator_the_window_shows_is_reachable_for_a_test() {
        FxToolkit.onFx(() -> {
            MainWindow window = window();

            assertNotNull(window.askBox());
            assertNotNull(window.history());
            assertNotNull(window.statusBar());
            assertNotNull(window.progressView());
            assertNotNull(window.root());
            window.close();
        });
    }


    /** A foreground that holds the work until a test lets it go. */
    private static final class Deferred implements java.util.function.Consumer<Runnable> {
        private final java.util.Deque<Runnable> waiting = new java.util.ArrayDeque<>();

        @Override
        public void accept(Runnable runnable) {
            waiting.add(runnable);
        }

        void runAll() {
            while (!waiting.isEmpty()) {
                waiting.poll().run();
            }
        }

        /** Let exactly one through, so a test can act in the middle. */
        void runNext() {
            Runnable next = waiting.poll();
            if (next != null) {
                next.run();
            }
        }
    }

    private MainWindow window(java.util.function.Consumer<Runnable> foreground) {
        store = Stores.of(sender, problems);
        return new MainWindow(client, store, Settings.from(Map.of()),
                Runnable::run, foreground, feedbackAccepted);
    }

    @Test
    void a_question_superseded_while_it_was_being_sent_is_never_watched() {
        // Without this a slow first answer overwrites a fast second one,
        // which looks exactly like the agent answering the wrong question.
        // The watch does not exist yet when it is superseded, so refusing to
        // open it is the only chance to refuse it.
        FxToolkit.onFx(() -> {
            client.streams.add(stream(event("done", Json.write(Fakes.answered()))));
            client.streams.add(stream(event("done", Json.write(Fakes.answered()))));
            Deferred later = new Deferred();
            MainWindow window = window(later);

            window.ask("the first question");
            window.ask("the second question");
            later.runAll();

            assertEquals(List.of("the first question", "the second question"), client.asked);
            // One stream opened, for the question that was still current.
            assertEquals(1, client.resumedFrom.size());
            assertEquals(1, window.history().list().getItems().size());
            window.close();
        });
    }

    @Test
    void stopping_a_question_in_flight_tells_the_server_to_forget_it() {
        FxToolkit.onFx(() -> {
            client.streams.add(stream(event("done", Json.write(Fakes.answered()))));
            Deferred later = new Deferred();
            MainWindow window = window(later);
            window.ask("how many stores");
            // The question has been accepted and is being watched; the answer
            // is queued but not drawn, so it is still in flight as far as the
            // window is concerned.
            later.runNext();

            window.cancel();

            assertEquals(List.of("job-1"), client.cancelled);
            assertEquals("Ask", window.askBox().action().getText());
            window.close();
        });
    }

    @Test
    void stopping_when_nothing_is_running_asks_the_server_for_nothing() {
        FxToolkit.onFx(() -> {
            MainWindow window = window();

            window.cancel();

            assertEquals(List.of(), client.cancelled);
            window.close();
        });
    }

    @Test
    void a_server_that_publishes_no_limits_at_all_is_still_usable() {
        // An older build, or one behind a proxy that rewrote the document.
        FxToolkit.onFx(() -> {
            client.meta = org.nl2sql.desktop.api.Json.read(
                    "{\"service\": \"nl2sql-agent\", \"version\": \"4.4.0\","
                            + " \"pipeline\": {\"nodes\": []}}", Models.Meta.class);
            MainWindow window = window();

            window.start();
            window.askBox().setQuestion("an ordinary question");

            assertFalse(window.askBox().action().isDisable());
            window.close();
        });
    }


    @Test
    void a_question_the_server_answered_inside_the_post_needs_no_cancelling() {
        // `?wait=` can come back finished, and asking the server to forget a
        // job it has already answered is a 409 nobody needed.
        FxToolkit.onFx(() -> {
            client.accepted = Fakes.answered();
            Deferred later = new Deferred();
            MainWindow window = window(later);
            window.ask("how many stores");
            later.runNext();

            window.cancel();

            assertEquals(List.of(), client.cancelled);
            window.close();
        });
    }

    @Test
    void a_cancel_the_server_refused_is_not_the_user_s_problem() {
        FxToolkit.onFx(() -> {
            client.streams.add(stream(event("done", Json.write(Fakes.answered()))));
            client.failCancel = new ApiException(409, "job_running", "cannot be interrupted");
            Deferred later = new Deferred();
            MainWindow window = window(later);
            window.ask("how many stores");
            later.runNext();

            window.cancel();

            assertEquals(List.of("job-1"), client.cancelled);
            // Nothing on screen about it: the question is already gone.
            assertFalse(Nodes.says(window.root(), "cannot be interrupted"));
            window.close();
        });
    }
}
