package org.nl2sql.desktop.ui;

import javafx.application.Platform;
import javafx.scene.Parent;
import javafx.scene.Scene;
import javafx.stage.Stage;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.extension.BeforeAllCallback;
import org.junit.jupiter.api.extension.ExtensionContext;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The JavaFX toolkit, started once for the whole run.
 *
 * <p>Headless, through Monocle -- see the dependency's note in {@code pom.xml}
 * for why the real windowing back end cannot be used from a test runner on
 * macOS. If the toolkit will not start at all the tests are skipped rather
 * than failed, because a machine that cannot draw is not a defect in this
 * application.
 *
 * <p>Everything is then run <em>on</em> the toolkit's thread rather than
 * through a robot. A test that presses a button with {@code fire()} and reads
 * the scene graph afterwards is exercising the same handlers a click would, in
 * one thread, with nothing to wait for -- which is what a robot costs and what
 * makes a robot flaky.
 */
public final class FxToolkit implements BeforeAllCallback {

    /** How long to give the toolkit before deciding this machine has none. */
    private static final int STARTUP_SECONDS = 30;

    private static final CountDownLatch STARTED = new CountDownLatch(1);

    private static boolean available;
    private static boolean tried;

    @Override
    public void beforeAll(ExtensionContext context) {
        Assumptions.assumeTrue(start(), "no JavaFX toolkit on this machine");
        // The toolkit's thread is not a daemon and implicit exit is off, so
        // nothing would end it when the last test does -- and a test JVM that
        // will not exit is one the build kills with a signal and reports as a
        // crash. Closing it is registered against the root context so it
        // happens once, after every class has run.
        context.getRoot()
                .getStore(ExtensionContext.Namespace.GLOBAL)
                .getOrComputeIfAbsent(ShutDown.class, key -> new ShutDown(), ShutDown.class);
    }

    static synchronized boolean start() {
        if (tried) {
            return available;
        }
        tried = true;
        try {
            Platform.startup(STARTED::countDown);
        } catch (IllegalStateException alreadyRunning) {
            // One startup per JVM. A second is somebody else's doing, and the
            // toolkit is up either way, which is all that is asked here.
            STARTED.countDown();
        } catch (RuntimeException noDisplay) {
            return false;
        }
        try {
            available = STARTED.await(STARTUP_SECONDS, TimeUnit.SECONDS);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
        if (available) {
            // Otherwise the toolkit shuts down with the first window that
            // closes, and every test after that one fails on a dead thread.
            Platform.setImplicitExit(false);
        }
        return available;
    }

    /** Run a test body on the toolkit's thread and wait for it, failures and all. */
    public static void onFx(Runnable body) {
        AtomicReference<Throwable> thrown = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        Platform.runLater(() -> {
            try {
                body.run();
            } catch (Throwable failure) {
                thrown.set(failure);
            } finally {
                done.countDown();
            }
        });
        try {
            if (!done.await(60, TimeUnit.SECONDS)) {
                throw new AssertionError("the JavaFX thread did not finish the test in 60 seconds");
            }
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new AssertionError("interrupted waiting for the JavaFX thread", interrupted);
        }
        Throwable failure = thrown.get();
        switch (failure) {
            case null -> {
                // Nothing went wrong, which is the usual outcome.
            }
            case RuntimeException runtime -> throw runtime;
            case Error error -> throw error;
            default -> throw new AssertionError(failure);
        }
    }

    /**
     * Put a node in a window and lay it out.
     *
     * <p>Needed for anything whose behaviour lives in a cell: a
     * {@code TableView} creates no cells until it has a skin, and a skin
     * until it is in a shown scene -- so a test that asserted on cells
     * without this would be asserting on a table that has none.
     */
    public static Stage render(Parent root) {
        return render(root, 900, 700);
    }

    /**
     * The same, at a chosen size, for the tests about reflowing.
     *
     * <p>Those tests build the thing twice, once per width, rather than
     * resizing one. Resizing a graph that has already been laid out leaves a
     * wrapped label reporting the height it had at the old width -- the
     * toolkit settles that over the next pulse, and a test measuring in
     * between reads it as text that did not reflow.
     */
    public static Stage render(Parent root, double width, double height) {
        Stage stage = new Stage();
        stage.setScene(Styles.apply(new Scene(root, width, height)));
        stage.show();
        settle(root);
        return stage;
    }

    /**
     * Lay out until it stops changing.
     *
     * <p>One pass is not enough for wrapped text. The pass that hands out the
     * new widths is not the pass that can ask how tall three lines are at
     * that width, so the first answer is the height the label had before --
     * which a single-pass test reads as text that did not reflow, and a
     * single-pass snapshot draws as one clipped line. A real window gets
     * another pulse; a test has to ask for one.
     */
    public static void settle(Parent root) {
        for (int pass = 0; pass < 8; pass++) {
            root.applyCss();
            root.layout();
            if (!root.isNeedsLayout()) {
                return;
            }
        }
    }

    /** Ends the toolkit when the whole run does. */
    private static final class ShutDown implements AutoCloseable {
        @Override
        public void close() {
            Platform.exit();
        }
    }
}
