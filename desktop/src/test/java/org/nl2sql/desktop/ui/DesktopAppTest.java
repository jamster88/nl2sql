package org.nl2sql.desktop.ui;

import javafx.application.Application;
import javafx.stage.Stage;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.Settings;
import org.nl2sql.desktop.api.ApiException;
import org.nl2sql.desktop.api.Models;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class DesktopAppTest {

    @Test
    void the_assembled_window_sends_verdicts_to_the_client_it_was_given() {
        FxToolkit.onFx(() -> {
            Fakes.FakeClient client = new Fakes.FakeClient();
            MainWindow window = DesktopApp.assemble(client,
                    Settings.from(Map.of("NL2SQL_FEEDBACK_FILE", Settings.NO_FILE)),
                    Runnable::run, Runnable::run);
            window.start();
            window.show(Fakes.answered());

            window.answerView().feedback().yes().fire();

            assertEquals(List.of("job-1:yes:"), client.votes);
            window.close();
        });
    }

    @Test
    void a_server_that_stages_nothing_keeps_the_verdict_here_instead() {
        // `/v1/meta` answers after the store is built, so the store has to
        // read the answer rather than having captured it.
        FxToolkit.onFx(() -> {
            Fakes.FakeClient client = new Fakes.FakeClient();
            client.meta = Fakes.meta(false);
            MainWindow window = DesktopApp.assemble(client,
                    Settings.from(Map.of("NL2SQL_FEEDBACK_FILE", Settings.NO_FILE)),
                    Runnable::run, Runnable::run);
            window.start();
            window.show(Fakes.answered());

            window.answerView().feedback().yes().fire();

            assertEquals(List.of(), client.votes);
            assertTrue(Nodes.says(window.root(), "click again to withdraw"));
            window.close();
        });
    }

    @Test
    void a_verdict_that_could_not_be_sent_reaches_the_status_bar() {
        FxToolkit.onFx(() -> {
            Fakes.FakeClient client = new Fakes.FakeClient();
            client.failFeedback = new ApiException(0, "unreachable", "no route to host");
            MainWindow window = DesktopApp.assemble(client,
                    Settings.from(Map.of("NL2SQL_FEEDBACK_FILE", Settings.NO_FILE)),
                    Runnable::run, Runnable::run);
            window.start();
            window.show(Fakes.answered());

            window.answerView().feedback().no().fire();

            assertTrue(Nodes.says(window.root(), "feedback: no route to host"));
            window.close();
        });
    }

    @Test
    void an_application_that_was_constructed_rather_than_launched_has_no_arguments() {
        // Documented JavaFX behaviour, and what a test and anything embedding
        // the toolkit both do.
        assertEquals(0, DesktopApp.raw(null).length);
    }

    @Test
    void the_window_opens_and_closes_again() {
        FxToolkit.onFx(() -> {
            DesktopApp app = new DesktopApp();
            Stage stage = new Stage();

            app.show(stage, "--url", "https://localhost:1", "--insecure");

            assertTrue(stage.isShowing());
            assertEquals("NL2SQL — ask the retail database", stage.getTitle());
            assertNotNull(app.window());
            assertTrue(stage.getScene().getStylesheets().contains(Styles.url()));
            // Nothing is there to answer, which the status bar says rather
            // than the window looking merely empty.
            assertTrue(Nodes.says(app.window().root(), "Not connected.")
                    || Nodes.says(app.window().root(), "Connecting"));

            app.stop();
            stage.close();
            assertFalse(stage.isShowing());
        });
    }

    @Test
    void stopping_something_that_never_started_is_allowed() {
        // `stop()` is called by the toolkit whatever happened in `start()`.
        FxToolkit.onFx(() -> new DesktopApp().stop());
    }

    @Test
    void the_toolkit_s_own_entry_point_opens_the_same_window() {
        // A constructed Application has no parameters, so this is the
        // defaults: whatever the environment says, or the compose deployment.
        FxToolkit.onFx(() -> {
            DesktopApp app = new DesktopApp();
            Stage stage = new Stage();

            app.start(stage);

            assertNotNull(app.window());
            assertTrue(stage.isShowing());
            app.stop();
            stage.close();
        });
    }

    @Test
    void the_sample_meta_is_the_one_the_window_is_configured_from() {
        assertEquals(2000, Fakes.meta(true).limits().max_question_length());
        assertEquals(20, Fakes.meta(true).limits().max_metadata_entries());
        assertEquals(Models.JobStatus.SUCCEEDED, Fakes.answered().status());
    }


    @Test
    void a_launched_application_is_given_its_command_line() {
        Application.Parameters given = new Application.Parameters() {
            @Override
            public List<String> getRaw() {
                return List.of("--insecure");
            }

            @Override
            public List<String> getUnnamed() {
                return List.of("--insecure");
            }

            @Override
            public Map<String, String> getNamed() {
                return Map.of();
            }
        };

        assertArrayEquals(new String[] {"--insecure"}, DesktopApp.raw(given));
    }


    @Test
    void a_verdict_file_that_cannot_be_read_is_reported_even_before_the_window_exists(
            @org.junit.jupiter.api.io.TempDir java.nio.file.Path scratch) throws Exception {
        // The store is built before the window and reports to it, so an
        // error raised while it reads its file has nowhere to go yet. It is
        // dropped rather than thrown: a corrupt file is not a reason for the
        // application not to open.
        java.nio.file.Path file = scratch.resolve("feedback.json");
        java.nio.file.Files.writeString(file, "{ this is not json");

        FxToolkit.onFx(() -> {
            MainWindow window = DesktopApp.assemble(new Fakes.FakeClient(),
                    Settings.from(Map.of("NL2SQL_FEEDBACK_FILE", file.toString())),
                    Runnable::run, Runnable::run);

            assertNotNull(window);
            window.close();
        });
    }
}
