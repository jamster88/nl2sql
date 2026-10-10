package org.nl2sql.desktop.ui;

import javafx.scene.control.Label;
import javafx.scene.control.Tooltip;
import javafx.scene.layout.FlowPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
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
 *
 * <p>Under the ensemble (7.0) the question is asked several ways and each
 * wording is a run of the whole pipeline, so one row of steps would be four
 * runs written over each other. Every event says which run it came from, and
 * the panel is drawn from that: a lane per run, each with the pipeline's
 * steps, and the ensemble's own steps above and below them -- what it did
 * before the runs, and what it did with their answers. On a host that serves
 * one model call at a time the lanes fill one after another, and side by side
 * when the deployer has allowed more; either way it is the order things
 * happened in. The web interface draws the same picture by the same rules
 * ({@code gui/src/components/Progress.tsx}).
 */
public final class ProgressView {

    private final Label current = new Label("Queued");
    private final Label count = new Label();
    private final VBox body = new VBox(8);
    private final VBox node = new VBox();

    private final List<Models.ProgressEvent> log = new ArrayList<>();
    private List<String> nodes = List.of();
    private Models.EnsembleSettings ensemble;
    private boolean done;

    public ProgressView() {
        current.getStyleClass().add("progress-current");
        // The detail a node reports is prose -- "3 tables, 2 examples" is the
        // short one -- so it is given the row and allowed to use two lines of
        // it rather than being cut off at the end of one.
        current.setWrapText(true);
        count.getStyleClass().addAll("progress-count", "muted");
        HBox head = new HBox(8, current, count);
        HBox.setHgrow(current, Priority.ALWAYS);
        head.getStyleClass().add("progress-head");

        node.getStyleClass().add("progress");
        node.setSpacing(8);
        node.getChildren().addAll(head, body);
        redraw();
    }

    public Region node() {
        return node;
    }

    /** Every node this server runs, in order. Empty before meta lands. */
    public void setNodes(List<String> pipelineNodes) {
        this.nodes = List.copyOf(pipelineNodes);
        redraw();
    }

    /**
     * How this server asks a question several ways, from {@code /v1/meta}:
     * how many runs to expect, and which of its nodes are the ensemble's own.
     * Null for a server older than 7.0.
     */
    public void setEnsemble(Models.EnsembleSettings settings) {
        this.ensemble = settings;
        redraw();
    }

    public void reset() {
        log.clear();
        done = false;
        current.setText("Queued");
        redraw();
    }

    public void add(Models.ProgressEvent event) {
        log.add(event);
        current.setText((event.candidate() == null ? "" : runName(event.candidate()) + ": ")
                + described(event));
        redraw();
    }

    public void finished() {
        done = true;
        int runs = started().size();
        current.setText("Finished — " + log.size() + (log.size() == 1 ? " step" : " steps")
                // A question refused before any run finished in no runs, and
                // says so by not mentioning them.
                + (runs == 0 ? "" : " in " + runs + (runs == 1 ? " run" : " runs")));
        redraw();
    }

    /** The run's name in the panel: the question as it was typed, or a rewording of it. */
    static String runName(int candidate) {
        return candidate == 0 ? "As asked" : "Rewording " + candidate;
    }

    private static String described(Models.ProgressEvent event) {
        return event.detail().isEmpty() ? event.label() : event.label() + " — " + event.detail();
    }

    /** The runs that have reported, in the order they began. */
    private List<Integer> started() {
        List<Integer> runs = new ArrayList<>();
        for (Models.ProgressEvent event : log) {
            if (event.candidate() != null && !runs.contains(event.candidate())) {
                runs.add(event.candidate());
            }
        }
        return runs;
    }

    private void redraw() {
        body.getChildren().clear();
        // The stream is the truth: an event naming a run is drawn in that
        // run's lane whether or not meta has said yet that there are runs.
        boolean laned = (ensemble != null && ensemble.enabled()) || !started().isEmpty();
        if (laned) {
            lanes();
        } else {
            single();
        }
    }

    /** One run of the pipeline: a server before 7.0, or one with the ensemble off. */
    private void single() {
        Map<String, Models.ProgressEvent> seen = latest(log);
        List<String> order = withExtras(nodes, seen);
        count.setText(order.isEmpty() ? "" : seen.size() + " / " + order.size());
        body.getChildren().add(steps(order, seen));
    }

    private void lanes() {
        List<Models.ProgressEvent> own = new ArrayList<>();
        Models.ProgressEvent first = null;
        for (Models.ProgressEvent event : log) {
            if (event.candidate() == null) {
                own.add(event);
            } else if (first == null) {
                first = event;
            }
        }

        // `nodes` is the ensemble's own and then each run's; the ensemble's
        // are named again by themselves, which is where the list divides.
        List<String> ownNodes = ensemble == null ? List.<String>of() : ensemble.nodes();
        Map<String, Models.ProgressEvent> seen = latest(own);
        List<String> outer = withExtras(ownNodes, seen);
        List<String> inner = nodes.subList(Math.min(ownNodes.size(), nodes.size()), nodes.size());

        // Above the lanes, what was done before any run began; below, the
        // rest. Until a run reports there is no telling which is which, so
        // they are all above and the lanes wait beneath them. A step that
        // never ran stays with its neighbours rather than dropping below: the
        // division is by place in the list, not by what has finished.
        int last = outer.size() - 1;
        boolean moved = false;
        if (first != null) {
            last = -1;
            for (Models.ProgressEvent event : own) {
                if (event.seq() < first.seq()) {
                    last = Math.max(last, outer.indexOf(event.step()));
                } else {
                    moved = true;
                }
            }
        }

        // A lane for every run that has reported, and a waiting lane for each
        // one still expected -- until the ensemble moves on to the answers,
        // when the runs that will happen have happened.
        List<Integer> runs = started();
        int expected = ensemble != null && ensemble.enabled() ? 1 + ensemble.paraphrases() : 0;
        int waiting = moved || done ? 0 : Math.max(0, expected - runs.size());
        int total = runs.size() + waiting;
        count.setText(total == 0 ? "" : runs.size() + " / " + total + " runs");

        if (last >= 0) {
            body.getChildren().add(steps(outer.subList(0, last + 1), seen));
        }
        Models.ProgressEvent newest = log.isEmpty() ? null : log.get(log.size() - 1);
        for (Integer run : runs) {
            List<Models.ProgressEvent> mine = new ArrayList<>();
            for (Models.ProgressEvent event : log) {
                if (run.equals(event.candidate())) {
                    mine.add(event);
                }
            }
            Models.ProgressEvent latest = mine.get(mine.size() - 1);
            Map<String, Models.ProgressEvent> reported = latest(mine);
            body.getChildren().add(lane(runName(run), described(latest),
                    !done && latest == newest ? "active" : "reported",
                    steps(withExtras(inner, reported), reported)));
        }
        for (int index = 0; index < waiting; index++) {
            body.getChildren().add(lane("Run " + (runs.size() + index + 1), "waiting", "waiting",
                    steps(inner, Map.of())));
        }
        if (last + 1 < outer.size()) {
            body.getChildren().add(steps(outer.subList(last + 1, outer.size()), seen));
        }
    }

    private static Region lane(String name, String status, String state, Region steps) {
        Label title = new Label(name);
        title.getStyleClass().add("progress-lane-name");
        Label where = new Label(status);
        where.getStyleClass().addAll("progress-lane-current", "muted");
        where.setWrapText(true);
        where.setMinWidth(0);
        HBox head = new HBox(10, title, where);
        HBox.setHgrow(where, Priority.ALWAYS);
        VBox lane = new VBox(6, head, steps);
        lane.getStyleClass().addAll("progress-lane", state);
        return lane;
    }

    /** Each step's latest event: a step that ran twice says what it did last. */
    private static Map<String, Models.ProgressEvent> latest(List<Models.ProgressEvent> events) {
        Map<String, Models.ProgressEvent> seen = new LinkedHashMap<>();
        for (Models.ProgressEvent event : events) {
            seen.put(event.step(), event);
        }
        return seen;
    }

    /** The listed steps, then any streamed step the list did not name. */
    private static List<String> withExtras(List<String> listed,
                                           Map<String, Models.ProgressEvent> seen) {
        List<String> order = new ArrayList<>(listed);
        for (String step : seen.keySet()) {
            if (!order.contains(step)) {
                order.add(step);
            }
        }
        return order;
    }

    private static Region steps(List<String> order, Map<String, Models.ProgressEvent> seen) {
        FlowPane steps = new FlowPane();
        steps.getStyleClass().add("progress-steps");
        steps.setHgap(6);
        steps.setVgap(6);
        for (String step : order) {
            Models.ProgressEvent event = seen.get(step);
            Label chip = new Label(event == null ? step : event.label());
            chip.getStyleClass().addAll("progress-step", event == null ? "pending" : "done");
            if (event != null && !event.detail().isEmpty()) {
                chip.setTooltip(new Tooltip(event.detail()));
            }
            steps.getChildren().add(chip);
        }
        return steps;
    }
}
