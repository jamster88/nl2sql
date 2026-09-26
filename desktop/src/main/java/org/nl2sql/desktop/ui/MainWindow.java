package org.nl2sql.desktop.ui;

import javafx.scene.Parent;
import javafx.scene.control.Label;
import javafx.scene.control.ScrollPane;
import javafx.scene.layout.BorderPane;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import org.nl2sql.desktop.Settings;
import org.nl2sql.desktop.api.ApiClient;
import org.nl2sql.desktop.api.ApiException;
import org.nl2sql.desktop.api.JobWatcher;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.api.Tls;
import org.nl2sql.desktop.feedback.FeedbackStore;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executor;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/**
 * The window, and everything wired to everything else.
 *
 * <p>One question at a time, because that is what the server does. What it
 * keeps is everything already answered this session, so comparing two answers
 * is a click.
 *
 * <p>Every collaborator is a constructor parameter, including the two that
 * decide which thread things happen on. That is not ceremony: it is what lets
 * the whole window be driven against a fake client with no server, no
 * container and no network, on one thread, in order -- which is the only way
 * these paths get exercised on every run. In the application {@code
 * background} is a pool and {@code foreground} is {@code Platform::runLater};
 * in a test both are "run it here".
 *
 * <p>The rule those two enforce is the one rule of a JavaFX application:
 * nothing that can block touches the toolkit's thread, and nothing that
 * touches the scene graph happens off it.
 */
public final class MainWindow {

    private final ApiClient client;
    private final FeedbackStore store;
    private final Settings settings;
    private final Executor background;
    private final Consumer<Runnable> foreground;
    /**
     * Whether this server takes feedback, which only {@code /v1/meta} knows
     * and only the sender asks. It is shared rather than returned because
     * meta answers after the store was built, and a store that captured the
     * value at construction would decide "no" before it had been told.
     */
    private final AtomicBoolean feedbackAccepted;

    private final AskBox askBox = new AskBox();
    private final ProgressView progress = new ProgressView();
    private final HistoryView history;
    private final StatusBar statusBar;
    private final VBox column = new VBox();
    private final VBox answerSlot = new VBox();
    private final Label error = new Label();
    private final Label notice = new Label();
    private final BorderPane root = new BorderPane();
    private final Runnable unsubscribe;

    private AnswerView answer;
    private JobWatcher watching;
    /** The question in flight, so cancelling has something to cancel. */
    private Models.Job pending;
    /** Which question is current, counted rather than named. See {@link #ask}. */
    private int attempt;
    private Models.Pipeline pipeline;
    private final List<String> warnings = new ArrayList<>();

    /** As wide as the session list gets, and a third of the window at most. */
    private static final double HISTORY_WIDTH = 260;

    public MainWindow(ApiClient client, FeedbackStore store, Settings settings, Executor background,
                      Consumer<Runnable> foreground, AtomicBoolean feedbackAccepted) {
        this.client = client;
        this.store = store;
        this.settings = settings;
        this.background = background;
        this.foreground = foreground;
        this.feedbackAccepted = feedbackAccepted;
        this.history = new HistoryView(store, settings.historyLimit());
        this.statusBar = new StatusBar(Tls.describe(settings));

        askBox.setOnAsk(this::ask);
        askBox.setOnCancel(this::cancel);
        history.setOnSelect(this::select);

        error.getStyleClass().addAll("notice", "notice-error");
        error.setWrapText(true);
        notice.getStyleClass().addAll("notice", "notice-quiet");
        notice.setWrapText(true);
        hide(error);
        hide(notice);
        hide(progress.node());

        column.getStyleClass().add("main");
        column.setSpacing(12);
        // Same rule as the status bar below: whatever is in the column, the
        // column is never the reason the window has to be wider.
        column.setMinWidth(0);
        column.getChildren().addAll(askBox.node(), error, notice, progress.node(), answerSlot);
        answerSlot.setSpacing(12);

        ScrollPane scroll = new ScrollPane(column);
        scroll.setFitToWidth(true);
        scroll.setMinWidth(0);
        scroll.getStyleClass().add("main-scroll");

        Label title = new Label("NL2SQL");
        title.getStyleClass().add("masthead-title");
        Label subtitle = new Label("ask the retail database");
        subtitle.getStyleClass().add("masthead-sub");
        VBox masthead = new VBox(2, title, subtitle);
        masthead.getStyleClass().add("masthead");

        Region left = history.node();
        left.setPrefWidth(HISTORY_WIDTH);
        left.setMinWidth(140);
        // A share of the window rather than a fixed strip. At 1100 the fixed
        // 260 was a quarter of it and right; at 560 it was very nearly half,
        // and the answer had to be read through what was left.
        left.maxWidthProperty().bind(
                javafx.beans.binding.Bindings.min(HISTORY_WIDTH, root.widthProperty().multiply(0.3)));
        VBox.setVgrow(left, Priority.ALWAYS);

        root.getStyleClass().add("app");
        root.setTop(masthead);
        root.setLeft(left);
        root.setCenter(scroll);
        root.setBottom(statusBar.node());

        this.unsubscribe = store.subscribe(() -> foreground.accept(this::feedbackChanged));
    }

    public Parent root() {
        return root;
    }

    /** Ask the server what it is, and whether it is ready. */
    public void start() {
        background.execute(() -> {
            try {
                Models.Meta meta = client.meta();
                foreground.accept(() -> describe(meta));
            } catch (ApiException cause) {
                foreground.accept(() -> statusBar.showError(cause.getMessage()));
            }
        });
        background.execute(() -> {
            try {
                Models.Readiness readiness = client.readiness();
                foreground.accept(() -> {
                    warnings.addAll(troubles(readiness));
                    statusBar.setWarnings(warnings);
                });
            } catch (ApiException ignored) {
                // Readiness is a courtesy. Its failure is not shown as a
                // connection failure -- `meta` above is what decides that, and
                // reporting both would say the same thing twice.
            }
        });
    }

    /**
     * Ask a question.
     *
     * <p>The attempt counter is what stops a slow first answer overwriting a
     * fast second one, which looks exactly like the agent answering the wrong
     * question. It is counted rather than keyed on the job id because the id
     * is not known until the POST comes back, and the race is two POSTs in
     * flight at once.
     */
    public void ask(String question) {
        String asked = question.trim();
        if (asked.isEmpty()) {
            return;
        }
        stopWatching();
        int mine = ++attempt;
        askBox.setBusy(true);
        askBox.setExamplesVisible(false);
        history.setCurrent(null);
        progress.reset();
        show(progress.node(), true);
        hide(error);
        hide(notice);
        answerSlot.getChildren().clear();

        background.execute(() -> {
            Models.Job accepted;
            try {
                accepted = client.ask(asked, settings.waitSeconds());
            } catch (ApiException cause) {
                onlyIfCurrent(mine, () -> failed(cause));
                return;
            }
            // Superseded while the POST was in flight. The watch could not
            // have been closed, because it did not exist yet -- so refusing
            // it here is the only chance.
            onlyIfCurrent(mine, () -> {
                // Remembered on this thread rather than the one that asked,
                // because `cancel()` reads it from here.
                pending = accepted;
                background.execute(() -> watch(accepted, mine));
            });
        });
    }

    /** Stop watching, and tell the server to forget it if it has not answered. */
    public void cancel() {
        Models.Job current = pending;
        stopWatching();
        attempt++;
        pending = null;
        askBox.setBusy(false);
        hide(progress.node());
        if (current == null || current.status() == Models.JobStatus.SUCCEEDED) {
            return;
        }
        String id = current.id();
        background.execute(() -> {
            try {
                client.cancel(id);
            } catch (ApiException ignored) {
                // It finished, or was already forgotten. Either way there is
                // nothing for the user to do about it, and the question is
                // off their screen already.
            }
        });
    }

    /** Let go of the watch and the store subscription. */
    public void close() {
        stopWatching();
        unsubscribe.run();
    }

    // --- for the tests, which press what a user presses ---------------------

    public AskBox askBox() {
        return askBox;
    }

    public HistoryView history() {
        return history;
    }

    public StatusBar statusBar() {
        return statusBar;
    }

    public ProgressView progressView() {
        return progress;
    }

    /** The answer on screen, or null when there is none. */
    public AnswerView answerView() {
        return answer;
    }

    // --- the machinery -----------------------------------------------------

    /**
     * Do this on the interface's thread, unless the question has moved on.
     *
     * <p>Said once rather than at each of the six places that needs it. A
     * stream arriving for a question the user has already replaced is
     * dropped, not drawn: without the check a slow first answer overwrites a
     * fast second one, which looks exactly like the agent answering the wrong
     * question.
     */
    private void onlyIfCurrent(int mine, Runnable action) {
        foreground.accept(() -> {
            if (mine == attempt) {
                action.run();
            }
        });
    }

    private void watch(Models.Job accepted, int mine) {
        JobWatcher watcher = new JobWatcher(client, accepted, new JobWatcher.Handlers() {
            @Override
            public void onProgress(Models.ProgressEvent event) {
                onlyIfCurrent(mine, () -> progress.add(event));
            }

            @Override
            public void onStatus(Models.JobStatus status) {
                // The status is already visible as the step that is running,
                // and a second place saying "running" is a second place to be
                // out of date.
            }

            @Override
            public void onDone(Models.Job finished) {
                onlyIfCurrent(mine, () -> answered(finished));
            }

            @Override
            public void onError(Exception cause) {
                onlyIfCurrent(mine, () -> say(cause.getMessage()));
            }

            @Override
            public void onFallback(String reason) {
                onlyIfCurrent(mine, () -> say(reason));
            }
        }, settings.pollIntervalMs());
        watching = watcher;
        watcher.run();
    }

    private void answered(Models.Job finished) {
        progress.finished();
        askBox.setBusy(false);
        pending = null;
        history.remember(finished);
        show(finished);
        history.setCurrent(finished.id());
    }

    private void failed(ApiException cause) {
        askBox.setBusy(false);
        hide(progress.node());
        error.setText("That question could not be sent. " + cause.getMessage());
        show(error, true);
    }

    private void say(String message) {
        notice.setText(message);
        show(notice, true);
    }

    /** Put an answer on screen. */
    public void show(Models.Job job) {
        answer = new AnswerView(job, store, pipeline);
        answerSlot.getChildren().setAll(answer.node());
        history.setCurrent(job.id());
    }

    /**
     * Show one picked out of the session list.
     *
     * <p>The progress panel goes with it: those steps belong to the question
     * that was just asked, and leaving them above an answer from ten minutes
     * ago reads as though that is how this one was arrived at.
     */
    private void select(Models.Job job) {
        hide(progress.node());
        show(job);
    }

    private void describe(Models.Meta meta) {
        pipeline = meta.pipeline();
        feedbackAccepted.set(meta.feedback());
        statusBar.show(meta);
        progress.setNodes(meta.pipeline().nodes());
        if (meta.limits() != null && meta.limits().max_question_length() > 0) {
            askBox.setMaxQuestionLength(meta.limits().max_question_length());
        }
        feedbackChanged();
    }

    /**
     * Something the user should know that is not about the question they
     * asked, such as a verdict that did not reach the server.
     */
    public void warn(String message) {
        if (!warnings.contains(message)) {
            warnings.add(message);
            statusBar.setWarnings(warnings);
        }
    }

    private static List<String> troubles(Models.Readiness readiness) {
        List<String> found = new ArrayList<>(readiness.warnings());
        for (Map.Entry<String, Models.Check> entry : readiness.checks().entrySet()) {
            if (!entry.getValue().ok()) {
                found.add(entry.getKey() + ": " + entry.getValue().detail());
            }
        }
        return found;
    }

    private void feedbackChanged() {
        if (answer != null) {
            answer.refreshFeedback();
        }
        history.refresh();
    }

    private void stopWatching() {
        if (watching != null) {
            watching.close();
            watching = null;
        }
    }

    private static void hide(Region target) {
        show(target, false);
    }

    private static void show(Region target, boolean showing) {
        target.setVisible(showing);
        target.setManaged(showing);
    }
}
