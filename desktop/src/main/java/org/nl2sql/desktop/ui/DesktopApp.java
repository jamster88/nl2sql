package org.nl2sql.desktop.ui;

import javafx.application.Application;
import javafx.application.Platform;
import javafx.scene.Scene;
import javafx.stage.Stage;
import org.nl2sql.desktop.Settings;
import org.nl2sql.desktop.api.ApiClient;
import org.nl2sql.desktop.api.HttpApiClient;
import org.nl2sql.desktop.feedback.ApiSender;
import org.nl2sql.desktop.feedback.FeedbackStore;

import java.time.Instant;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The application, assembled.
 *
 * <p>Everything interesting is in {@link MainWindow}; this is the twenty lines
 * that decide what it is given -- a real HTTP client, a thread pool, a file to
 * keep verdicts in -- and hands the toolkit a window. Keeping those apart is
 * what lets the window be tested: an {@code Application} cannot be constructed
 * twice in one JVM, and everything worth asserting would otherwise be behind
 * that.
 */
public final class DesktopApp extends Application {

    private ExecutorService background;
    private MainWindow window;

    /** The window on screen, or null before {@link #start} has run. */
    MainWindow window() {
        return window;
    }

    @Override
    public void start(Stage stage) {
        show(stage, raw(getParameters()));
    }

    /**
     * Everything {@link #start} does, given the arguments rather than asking
     * the toolkit for them.
     *
     * <p>Split so a window can be opened from a test. {@code launch()} can be
     * called once per JVM and does not return until the last window closes,
     * so a test that went through it would be the only test in the run.
     */
    void show(Stage stage, String... arguments) {
        Settings settings = Settings.from(System.getenv(), arguments);
        background = pool();
        window = assemble(new HttpApiClient(settings), settings, background, Platform::runLater);

        stage.setTitle("NL2SQL — ask the retail database");
        stage.setScene(Styles.apply(new Scene(window.root(), 1100, 760)));
        stage.show();
        window.start();
    }

    /**
     * The command line, or nothing at all.
     *
     * <p>{@code getParameters()} is null for an {@code Application} that was
     * constructed rather than launched -- which is what a test does, and what
     * anything embedding JavaFX does. It is documented behaviour rather than
     * an edge case, and the answer to it is "no arguments".
     */
    static String[] raw(Parameters parameters) {
        return parameters == null ? new String[0] : parameters.getRaw().toArray(new String[0]);
    }

    @Override
    public void stop() {
        if (window != null) {
            window.close();
        }
        if (background != null) {
            background.shutdownNow();
        }
    }

    /**
     * The window with a store and a sender wired to this client.
     *
     * <p>Static and public so a test gets the same assembly the application
     * does, rather than a second one written to be convenient.
     */
    public static MainWindow assemble(ApiClient client, Settings settings,
                                      java.util.concurrent.Executor background,
                                      java.util.function.Consumer<Runnable> foreground) {
        AtomicBoolean accepted = new AtomicBoolean();
        // The store is built before the window and reports to it, so the
        // reference is filled in a moment later. A lambda capturing the holder
        // is the whole of the knot: the alternative is a window that cannot
        // say a verdict failed to send until the user scrolls back to it.
        AtomicReference<MainWindow> built = new AtomicReference<>();
        FeedbackStore store = new FeedbackStore(
                new ApiSender(client, accepted::get),
                settings.feedbackFile(),
                background,
                Instant::now,
                cause -> foreground.accept(() -> {
                    MainWindow window = built.get();
                    if (window != null) {
                        window.warn("feedback: " + cause.getMessage());
                    }
                }));
        MainWindow window = new MainWindow(client, store, settings, background, foreground, accepted);
        built.set(window);
        return window;
    }

    /**
     * Daemon threads, so closing the window ends the process.
     *
     * <p>A watcher parked on an event stream is waiting on a socket the server
     * has no reason to close, and a non-daemon thread doing that keeps a JVM
     * alive after its last window has gone -- which looks, from the outside,
     * exactly like a crash that did not clean up.
     */
    private static ExecutorService pool() {
        return Executors.newCachedThreadPool(runnable -> {
            Thread thread = new Thread(runnable, "nl2sql-desktop");
            thread.setDaemon(true);
            return thread;
        });
    }
}
