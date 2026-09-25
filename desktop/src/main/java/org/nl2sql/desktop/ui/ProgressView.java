package org.nl2sql.desktop.ui;

import javafx.scene.control.Label;
import javafx.scene.control.Tooltip;
import javafx.scene.layout.FlowPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import org.nl2sql.desktop.api.Models;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * What the agent is doing, while it does it.
 *
 * <p>The steps are the pipeline's own graph nodes, streamed as they finish, so
 * this is a report rather than an animation -- which is the point. A user
 * watching "matching literals" go by learns something about why the answer
 * took a minute; a user watching a spinner learns that the application might
 * be broken.
 *
 * <p>The full node list comes from {@code /v1/meta}, so the steps still to
 * come are shown greyed rather than appearing one at a time out of nowhere.
 * Anything streamed that is not in that list is still shown, because the
 * stream is the truth and the list is only a prediction.
 */
public final class ProgressView {

    private final Label current = new Label("Queued");
    private final Label count = new Label();
    private final FlowPane steps = new FlowPane();
    private final VBox node = new VBox();

    private final Map<String, Models.ProgressEvent> seen = new LinkedHashMap<>();
    private List<String> nodes = List.of();
    private int events;

    public ProgressView() {
        current.getStyleClass().add("progress-current");
        count.getStyleClass().addAll("progress-count", "muted");
        HBox head = new HBox(8, current, count);
        head.getStyleClass().add("progress-head");

        steps.getStyleClass().add("progress-steps");
        steps.setHgap(6);
        steps.setVgap(6);

        node.getStyleClass().add("progress");
        node.setSpacing(8);
        node.getChildren().addAll(head, steps);
    }

    public Region node() {
        return node;
    }

    /** Every node this server runs, in order. Empty before meta lands. */
    public void setNodes(List<String> pipelineNodes) {
        this.nodes = List.copyOf(pipelineNodes);
        redraw();
    }

    public void reset() {
        seen.clear();
        events = 0;
        current.setText("Queued");
        redraw();
    }

    public void add(Models.ProgressEvent event) {
        seen.put(event.step(), event);
        events++;
        current.setText(event.detail().isEmpty()
                ? event.label()
                : event.label() + " — " + event.detail());
        redraw();
    }

    public void finished() {
        current.setText("Finished — " + events + (events == 1 ? " step" : " steps"));
        redraw();
    }

    private void redraw() {
        List<String> order = new ArrayList<>(nodes);
        for (String step : seen.keySet()) {
            if (!order.contains(step)) {
                order.add(step);
            }
        }
        count.setText(order.isEmpty() ? "" : seen.size() + " / " + order.size());

        steps.getChildren().clear();
        for (String step : order) {
            Models.ProgressEvent event = seen.get(step);
            Label chip = new Label(event == null ? step : event.label());
            chip.getStyleClass().addAll("progress-step", event == null ? "pending" : "done");
            if (event != null && !event.detail().isEmpty()) {
                chip.setTooltip(new Tooltip(event.detail()));
            }
            steps.getChildren().add(chip);
        }
    }
}
