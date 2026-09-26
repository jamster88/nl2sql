package org.nl2sql.desktop.ui;

import javafx.collections.FXCollections;
import javafx.collections.ObservableList;
import javafx.scene.control.Label;
import javafx.scene.control.ListCell;
import javafx.scene.control.ListView;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.feedback.FeedbackRecord;
import org.nl2sql.desktop.feedback.FeedbackStore;

import java.util.function.Consumer;

/**
 * Everything answered this session.
 *
 * <p>One question at a time is what the server does -- questions queue behind
 * each other on a single chat host, so a client that let four run at once
 * would only be four progress bars. What it can keep is everything already
 * answered, so comparing two answers is a click.
 *
 * <p>Each row carries the verdict given to it, if any, which is the only place
 * a user can see at a glance what they have already said about what.
 */
public final class HistoryView {

    private final ObservableList<Models.Job> jobs = FXCollections.observableArrayList();
    private final ListView<Models.Job> list = new ListView<>(jobs);
    private final VBox node = new VBox();
    private final FeedbackStore store;
    private final int limit;

    /** The cell's own padding, plus room for the scroll bar the list may show. */
    private static final int CELL_MARGIN = 36;

    private Consumer<Models.Job> onSelect = job -> {
    };
    private boolean selecting;

    public HistoryView(FeedbackStore store, int limit) {
        this.store = store;
        this.limit = limit;

        Label heading = new Label("This session");
        heading.getStyleClass().add("history-heading");

        list.getStyleClass().add("history");
        list.setPlaceholder(mutedPlaceholder());
        list.setCellFactory(unused -> new Row());
        list.getSelectionModel().selectedItemProperty().addListener((observable, before, after) -> {
            // A selection this class made itself is not a click, and treating
            // it as one would re-enter the handler that made it.
            if (after != null && !selecting) {
                onSelect.accept(after);
            }
        });

        node.getStyleClass().add("history-panel");
        node.setSpacing(6);
        node.getChildren().addAll(heading, list);
        VBox.setVgrow(list, javafx.scene.layout.Priority.ALWAYS);
    }

    public Region node() {
        return node;
    }

    public ListView<Models.Job> list() {
        return list;
    }

    public void setOnSelect(Consumer<Models.Job> handler) {
        this.onSelect = handler;
    }

    /** Put a finished job at the top, replacing any earlier run of the same id. */
    public void remember(Models.Job job) {
        jobs.removeIf(existing -> existing.id().equals(job.id()));
        jobs.add(0, job);
        while (jobs.size() > limit) {
            jobs.remove(jobs.size() - 1);
        }
    }

    /** Show which answer is on screen, without calling the selection handler. */
    public void setCurrent(String jobId) {
        selecting = true;
        if (jobId == null) {
            list.getSelectionModel().clearSelection();
        } else {
            for (Models.Job job : jobs) {
                if (job.id().equals(jobId)) {
                    list.getSelectionModel().select(job);
                    break;
                }
            }
        }
        selecting = false;
    }

    /** Redraw the verdict marks after a vote. */
    public void refresh() {
        list.refresh();
    }

    private static Label mutedPlaceholder() {
        Label label = new Label("Answers appear here as they arrive.");
        label.getStyleClass().add("muted");
        label.setWrapText(true);
        return label;
    }

    /**
     * One question in the list.
     *
     * <p>The question is drawn by a label of the cell's own rather than by
     * the cell's own text, because a {@code ListCell} sizes itself to its
     * text and the virtual flow behind it sizes itself to the widest cell --
     * so one long question made the whole panel wider than the pane holding
     * it, and the list spilled out over the answer beside it. A label with a
     * ceiling read from the list wraps instead.
     */
    private final class Row extends ListCell<Models.Job> {

        private final Label text = new Label();

        Row() {
            text.setWrapText(true);
            text.maxWidthProperty().bind(list.widthProperty().subtract(CELL_MARGIN));
            // Never ask for more room than the list has: the cell's own
            // preferred width is what the flow behind it adds up.
            setPrefWidth(0);
            setMinWidth(0);
        }

        @Override
        protected void updateItem(Models.Job job, boolean empty) {
            super.updateItem(job, empty);
            // `empty` and a null job are the same state: a cell the list is
            // holding for a row that is not there.
            if (job == null) {
                setGraphic(null);
                setTooltip(null);
                return;
            }
            String mark = store.get(job.id())
                    .map(Row::glyph)
                    .orElse("");
            text.setText(mark + job.question());
            setGraphic(text);
            setTooltip(new javafx.scene.control.Tooltip(job.question()));
        }

        private static String glyph(FeedbackRecord record) {
            return switch (record.verdict()) {
                case YES -> "\u2713  ";
                case NO -> "\u2717  ";
                // Half a circle: right, and not all of it.
                case INCOMPLETE -> "\u25D0  ";
            };
        }
    }
}
