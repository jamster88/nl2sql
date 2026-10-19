package org.nl2sql.desktop.ui;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.Settings;
import org.nl2sql.desktop.api.ApiException;
import org.nl2sql.desktop.api.Json;
import org.nl2sql.desktop.api.Models;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The window's conversation (8.0): the id it makes and sends, the button that
 * makes another, the "Read as" line under a follow-up, and the status bar's
 * sentence about what the server remembers.
 */
@ExtendWith(FxToolkit.class)
class ConversationTest {

    private static final Pattern UUID = Pattern.compile(
            "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}");
    private static final String STANDALONE = "How many stores are there in each state?";

    private final Fakes.FakeClient client = new Fakes.FakeClient();

    private static Models.Conversation conversation(String readAs, boolean resolved) {
        return new Models.Conversation("c-1", 2, 1, "And in each state?", readAs, resolved,
                resolved ? "" : "not restated");
    }

    private static Models.Job asked(Models.Answer answer) {
        Models.Job base = Fakes.answered();
        return new Models.Job(base.id(), base.status(), "And in each state?", base.metadata(),
                base.created_at(), base.started_at(), base.finished_at(), base.duration_ms(),
                base.progress(), answer, null, base.links(), null);
    }

    private static AnswerView view(Models.Answer answer) {
        return new AnswerView(asked(answer), Stores.of(new Stores.Recording()), Fakes.meta(true).pipeline());
    }

    private MainWindow window() {
        return new MainWindow(client, new Fakes.FakeSignIn(), Stores.of(new Stores.Recording()),
                Settings.from(Map.of(), new String[0]), Runnable::run, Runnable::run, new AtomicBoolean());
    }

    /** Ask, and let the stream answer it. */
    private void answer(MainWindow window, String question) {
        client.streams.add(List.of("event: done", "data: " + Json.write(Fakes.answered()), ""));
        window.ask(question);
    }

    // --- the status bar ------------------------------------------------------

    @Test
    void the_status_bar_says_what_the_server_remembers_and_which_pipeline_answers() {
        FxToolkit.onFx(() -> {
            StatusBar bar = new StatusBar("verified");
            bar.show(Fakes.remembering(5, "v6_3_1"));
            assertTrue(Nodes.says(bar.node(), "remembers the last 5 turns"));
            assertTrue(Nodes.says(bar.node(), "pipeline v6_3_1"));

            bar.show(Fakes.remembering(1, "v7_0_0"));
            assertTrue(Nodes.says(bar.node(), "remembers the last turn"));

            bar.show(Fakes.remembering(0, "v7_0_0"));
            assertTrue(Nodes.says(bar.node(), "each question asked alone"));
        });
    }

    @Test
    void a_server_older_than_8_0_asks_each_question_alone_and_names_no_pipeline() {
        FxToolkit.onFx(() -> {
            StatusBar bar = new StatusBar("verified");
            bar.show(Fakes.meta(true));
            assertTrue(Nodes.says(bar.node(), "each question asked alone"));
            assertFalse(Nodes.says(bar.node(), "pipeline "));
            assertEquals(0, StatusBar.remembers(Fakes.meta(true)));
        });
    }

    // --- the button ----------------------------------------------------------

    @Test
    void the_button_is_there_only_when_there_is_a_conversation_to_leave() {
        FxToolkit.onFx(() -> {
            List<String> pressed = new ArrayList<>();
            AskBox box = new AskBox();
            box.setOnNewConversation(() -> pressed.add("new"));
            assertFalse(box.newConversation().isVisible());
            assertFalse(box.newConversation().isManaged());

            box.setConversational(true);
            assertTrue(box.newConversation().isVisible());
            box.newConversation().fire();
            assertEquals(List.of("new"), pressed);

            // It waits while a question is answered, as the box does.
            box.setBusy(true);
            assertTrue(box.newConversation().isDisable());
            box.setBusy(false);
            assertFalse(box.newConversation().isDisable());
        });
    }

    @Test
    void the_buttons_default_does_nothing() {
        FxToolkit.onFx(() -> {
            AskBox box = new AskBox();
            box.setConversational(true);
            box.newConversation().fire();
            assertTrue(box.newConversation().isVisible());
        });
    }

    // --- Read as -------------------------------------------------------------

    @Test
    void a_restated_follow_up_says_how_it_was_read_under_the_words_typed() {
        FxToolkit.onFx(() -> {
            AnswerView view = view(Fakes.answer(conversation(STANDALONE, true)));

            assertTrue(Nodes.says(view.node(), "And in each state?"));
            javafx.scene.Node line = Nodes.oneWithClass(view.node(), "answer-read-as");
            assertEquals(List.of("Read as: ", STANDALONE, " · turn 2, 1 remembered"), Nodes.texts(line));
            assertEquals(1, Nodes.withClass(line, "read-as-reading").size());
            // Under the question: the head first, then the line.
            assertEquals(line, view.node().getChildrenUnmodifiable().get(1));
        });
    }

    @Test
    void nothing_is_said_of_a_question_that_was_not_restated() {
        FxToolkit.onFx(() -> {
            for (Models.Conversation none : Arrays.asList(null, conversation("", false),
                    conversation(STANDALONE, false), conversation("", true))) {
                AnswerView view = view(Fakes.answer(none));
                assertTrue(Nodes.withClass(view.node(), "answer-read-as").isEmpty(), String.valueOf(none));
            }
            assertEquals("", AnswerView.readAs(null));
        });
    }

    @Test
    void the_reading_is_not_taken_for_the_answer_when_there_are_no_claims() {
        FxToolkit.onFx(() -> {
            Models.Answer base = Fakes.answer();
            Models.Answer bare = new Models.Answer(
                    "Read as: *" + STANDALONE + "*\n\nThere are stores in 12 states.\n\n| a |", "",
                    base.sql(), "proceed", base.intent(), null, base.tables(), base.literals(),
                    base.result(), base.chart(), List.of(), base.audit(), base.plan_cost(),
                    base.attempts(), base.trace(), Map.of(), Map.of(), null, "v7_0_0",
                    conversation(STANDALONE, true));

            AnswerView view = view(bare);
            assertTrue(Nodes.says(Nodes.oneWithClass(view.node(), "answer-headline"),
                    "There are stores in 12 states."));
            assertFalse(Nodes.says(Nodes.oneWithClass(view.node(), "answer-headline"), "Read as"));
        });
    }

    // --- the window ----------------------------------------------------------

    @Test
    void the_window_sends_one_id_with_every_question_and_a_new_one_after_the_button() {
        FxToolkit.onFx(() -> {
            client.meta = Fakes.remembering(5, "v7_0_0");
            MainWindow window = window();
            window.start();
            assertTrue(window.askBox().newConversation().isVisible());

            answer(window, "How many stores are there?");
            answer(window, "And in each state?");
            String first = client.conversations.get(0);
            assertTrue(UUID.matcher(first).matches(), first);
            assertEquals(first, client.conversations.get(1));
            assertEquals(first, window.conversation());
            int listed = window.history().list().getItems().size();

            window.askBox().newConversation().fire();
            assertTrue(Nodes.says(window.root(), "A new conversation: the next question is read without "
                    + "the ones before it."));
            // The session list is the questions answered, not the conversation.
            assertEquals(listed, window.history().list().getItems().size());

            answer(window, "How many products are there?");
            String third = client.conversations.get(2);
            assertTrue(UUID.matcher(third).matches(), third);
            assertNotEquals(first, third);
            assertFalse(Nodes.says(window.root(), "A new conversation"));
            window.close();
        });
    }

    @Test
    void a_window_names_no_conversation_to_a_server_that_keeps_none() {
        FxToolkit.onFx(() -> {
            client.meta = Fakes.remembering(0, "v7_0_0");
            MainWindow window = window();
            window.start();

            answer(window, "How many stores are there?");

            assertEquals(Arrays.asList((String) null), client.conversations);
            assertFalse(window.askBox().newConversation().isVisible());
            window.close();
        });
    }

    @Test
    void two_windows_are_two_conversations() {
        FxToolkit.onFx(() -> {
            MainWindow one = window();
            MainWindow two = window();
            assertNotEquals(one.conversation(), two.conversation());
            one.close();
            two.close();
        });
    }

    @Test
    void a_question_refused_because_the_last_is_still_answering_is_said_as_any_refusal() {
        FxToolkit.onFx(() -> {
            client.meta = Fakes.remembering(5, "v7_0_0");
            client.failAsk = new ApiException(409, "conversation_busy",
                    "conversation c-1 is still answering its last question; ask the next once that one has finished");
            MainWindow window = window();
            window.start();

            window.ask("and again?");

            assertTrue(Nodes.says(window.root(), "That question could not be sent."));
            assertTrue(Nodes.says(window.root(), "still answering its last question"));
            window.close();
        });
    }
}
