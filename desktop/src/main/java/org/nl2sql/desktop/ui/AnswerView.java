package org.nl2sql.desktop.ui;

import javafx.geometry.Pos;
import javafx.scene.control.Label;
import javafx.scene.control.TextArea;
import javafx.scene.control.TitledPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import javafx.scene.text.TextFlow;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.chart.Values;
import org.nl2sql.desktop.feedback.FeedbackStore;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * The answer.
 *
 * <p>Ordered by what a reader wants in the order they want it: the sentence
 * first, then the picture, then the rows, then -- folded away -- the SQL, the
 * matched literals and the per-node cost that say how the sentence was arrived
 * at. Someone who trusts the agent never opens the last three; someone
 * checking it opens all of them, and they are all there.
 *
 * <p>Two things about the sentence are worth knowing, because both look like
 * bugs in this file if they are not:
 *
 * <ul>
 *   <li><b>It comes from {@code narrative}, not {@code answer}.</b>
 *       {@code answer} is a whole markdown document -- the prose, then the
 *       rows as a markdown table, then the caveats -- which is what a terminal
 *       wants. Drawing it here would print the pipe characters of a table this
 *       window is already drawing.
 *   <li><b>It is unescaped on the way in.</b> The prose is escaped for
 *       markdown, which renders raw HTML; a label draws text, so {@code &amp;}
 *       would arrive on screen as five characters. See {@link Text}.
 * </ul>
 *
 * <p>And the sentence is not one block: the audit ties each of its sentences to
 * the cells it was read from, so each is its own hoverable span and pointing
 * at one lights up its cells in the table.
 *
 * <p>Three cases are not an answer at all and are handled before any of it. A
 * <b>refusal is a success</b> -- the agent decided the question was out of
 * scope or unsafe and said so, and there is no table because no query ran. An
 * <b>ambiguous</b> question comes back with a clarification, which is a
 * question for the user, so it goes where an answer would be. A <b>failed
 * run</b> still carries the SQL it tried and the attempts it made, and those
 * are the useful part.
 */
public final class AnswerView {

    /** The labels the pipeline's own verdicts get in the interface. */
    public static final Map<String, String> VERDICT_TITLES = Map.of(
            "refused", "The agent declined this question",
            "out_of_domain", "That is outside what this database covers",
            "ambiguous", "That question has more than one answer");

    private final VBox node = new VBox();
    private final FeedbackBar feedback = new FeedbackBar();
    private final FeedbackStore store;
    private final Models.Job job;
    private ResultTableView table;

    public AnswerView(Models.Job job, FeedbackStore store, Models.Pipeline pipeline) {
        this.job = job;
        this.store = store;

        node.getStyleClass().add("answer");
        node.setSpacing(12);
        node.getChildren().add(head());

        Models.Answer answer = job.answer();
        if (job.status() == Models.JobStatus.FAILED || answer == null) {
            node.getChildren().add(failure());
        } else if (!answer.verdict().equals("proceed")) {
            node.getChildren().add(refusal(answer));
        } else {
            node.getChildren().add(narrative(answer));
            if (answer.result() != null && answer.chart() != null) {
                Region chart = ChartView.of(answer.result(), answer.chart());
                if (chart != null) {
                    node.getChildren().add(chart);
                }
            }
            if (answer.result() != null) {
                table = new ResultTableView(answer.result());
                node.getChildren().addAll(table.node(), ResultTableView.caption(answer.result()));
            }
            if (pipeline == null || pipeline.audit()) {
                node.getChildren().add(audit(answer));
            }
        }

        node.getChildren().addAll(details(answer));
        wireFeedback();
        node.getChildren().add(feedback.node());
    }

    public Region node() {
        return node;
    }

    /** The bar, for the tests and for the window that refreshes it. */
    public FeedbackBar feedback() {
        return feedback;
    }

    /** The table, or null when this answer has none. */
    public ResultTableView table() {
        return table;
    }

    /** Redraw the verdict from the store, after a vote or a send finishing. */
    public void refreshFeedback() {
        feedback.setCommentable(store.sends());
        feedback.show(store.get(job.id()).orElse(null));
    }

    // --- the parts ---------------------------------------------------------

    private Region head() {
        Label question = new Label(job.question());
        question.getStyleClass().add("answer-question");
        question.setWrapText(true);
        HBox row = new HBox(12, question);
        HBox.setHgrow(question, Priority.ALWAYS);
        row.setAlignment(Pos.CENTER_LEFT);
        if (job.duration_ms() != null) {
            Label timing = new Label(String.format(Locale.US, "%.1fs", job.duration_ms() / 1000));
            timing.getStyleClass().addAll("answer-timing", "muted");
            row.getChildren().add(timing);
        }
        row.getStyleClass().add("answer-head");
        return row;
    }

    private Region failure() {
        VBox box = new VBox(6);
        box.getStyleClass().addAll("notice", "notice-error");
        Label headline = new Label("The run failed.");
        headline.getStyleClass().add("notice-headline");
        Label why = new Label(job.error() == null || job.error().isEmpty()
                ? "The server did not say why."
                : job.error());
        why.setWrapText(true);
        box.getChildren().addAll(headline, why);
        Models.Answer answer = job.answer();
        if (answer != null && !answer.sql().isEmpty()) {
            box.getChildren().add(sql(answer.sql()));
        }
        return box;
    }

    private static Region refusal(Models.Answer answer) {
        VBox box = new VBox(6);
        box.getStyleClass().addAll("notice", "notice-verdict");
        Label headline = new Label(
                VERDICT_TITLES.getOrDefault(answer.verdict(), "The agent did not answer"));
        headline.getStyleClass().add("notice-headline");
        String said = answer.clarification() == null ? "" : Text.plain(answer.clarification());
        Label body = new Label(said.isEmpty() ? Text.lead(answer.answer()) : said);
        body.setWrapText(true);
        box.getChildren().addAll(headline, body);
        return box;
    }

    /**
     * The answer as a paragraph whose sentences are each traceable.
     *
     * <p>Falls back to the prose at the top of the markdown answer when there
     * are no claims to hang the sentences on -- which is what happens on a
     * server running without the narrator, and for any answer the audit
     * dropped every claim from.
     */
    private Region narrative(Models.Answer answer) {
        if (answer.claims().isEmpty()) {
            String said = Text.plain(answer.narrative());
            Label headline = new Label(said.isEmpty() ? Text.lead(answer.answer()) : said);
            headline.getStyleClass().add("answer-headline");
            headline.setWrapText(true);
            return headline;
        }

        TextFlow flow = new TextFlow();
        flow.getStyleClass().add("answer-headline");
        for (Models.Claim claim : answer.claims()) {
            Label sentence = new Label(Text.plain(claim.text())
                    + (claim.formula() == null ? "" : " " + claim.formula()) + " ");
            sentence.getStyleClass().add("claim");
            Set<String> cells = keysOf(claim);
            sentence.setOnMouseEntered(event -> light(cells));
            sentence.setOnMouseExited(event -> light(Set.of()));
            flow.getChildren().add(sentence);
        }
        return flow;
    }

    /** The cells a claim was read from, as the table names them. */
    static Set<String> keysOf(Models.Claim claim) {
        Set<String> keys = new HashSet<>();
        for (List<Object> cell : claim.cells()) {
            if (cell.size() < 2) {
                continue;
            }
            Double row = Values.toNumber(cell.get(0));
            if (row != null) {
                keys.add(ResultTableView.cellKey(row.intValue(), Values.toLabel(cell.get(1))));
            }
        }
        return keys;
    }

    private void light(Set<String> keys) {
        if (table != null) {
            table.highlight(keys);
        }
    }

    private static Region audit(Models.Answer answer) {
        Models.AuditReport report = answer.audit();
        List<String> problems = new ArrayList<>();
        for (String claim : report.unsupported_claims()) {
            problems.add("Unsupported: " + Text.plain(claim));
        }
        for (String reason : report.drop_reasons()) {
            problems.add(Text.plain(reason));
        }
        if (report.semantic_issue() != null && !report.semantic_issue().isEmpty()) {
            problems.add(Text.plain(report.semantic_issue()));
        }

        if (report.passed() && problems.isEmpty()) {
            Label passed = new Label(
                    "✓  Every number in this answer was checked against the rows above.");
            passed.getStyleClass().addAll("audit", "audit-passed");
            passed.setWrapText(true);
            return passed;
        }

        VBox box = new VBox(4);
        box.getStyleClass().addAll("audit", "audit-failed");
        Label headline = new Label("!  The audit found something.");
        headline.getStyleClass().add("notice-headline");
        box.getChildren().add(headline);
        for (String problem : problems) {
            Label line = new Label("•  " + problem);
            line.setWrapText(true);
            box.getChildren().add(line);
        }
        return box;
    }

    private List<Region> details(Models.Answer answer) {
        List<Region> panes = new ArrayList<>();
        if (answer == null) {
            return panes;
        }
        if (!answer.sql().isEmpty()) {
            VBox body = new VBox(6, sql(answer.sql()));
            if (!answer.tables().isEmpty()) {
                body.getChildren().add(muted("Tables: " + String.join(", ", answer.tables())));
            }
            if (answer.plan_cost() != null) {
                body.getChildren().add(muted("Planner cost estimate: "
                        + String.format(Locale.US, "%,.1f", answer.plan_cost())));
            }
            panes.add(disclosure(answer.attempts() > 1
                    ? "SQL (" + answer.attempts() + " attempts)"
                    : "SQL", body));
        }
        if (!answer.literals().isEmpty()) {
            VBox body = new VBox(2);
            for (Models.LiteralMatch literal : answer.literals()) {
                body.getChildren().add(muted(String.format(Locale.US, "%s → %s  in %s.%s (%.2f)",
                        literal.phrase(), literal.value(), literal.table(), literal.column(),
                        literal.score())));
            }
            panes.add(disclosure("Matched values (" + answer.literals().size() + ")", body));
        }
        if (!answer.trace().isEmpty()) {
            panes.add(disclosure("How long each step took (" + answer.trace().size() + " steps)",
                    trace(answer)));
        }
        return panes;
    }

    /**
     * Per-node cost, as a bar per row.
     *
     * <p>Not a chart: it is a list that happens to be sorted, and drawing it
     * with the chart machinery would put an axis and a legend around eight
     * numbers nobody compares across questions.
     */
    private static Region trace(Models.Answer answer) {
        double slowest = 1;
        for (Models.TraceEntry entry : answer.trace()) {
            slowest = Math.max(slowest, entry.ms());
        }
        VBox rows = new VBox(2);
        rows.getStyleClass().add("trace");
        for (Models.TraceEntry entry : answer.trace()) {
            Label name = new Label(entry.node());
            name.getStyleClass().add("trace-node");
            name.setMinWidth(160);
            Region bar = new Region();
            bar.getStyleClass().add("trace-bar");
            bar.setPrefWidth(Math.max(1, entry.ms() / slowest * 240));
            Label ms = new Label(String.format(Locale.US, "%.2fs", entry.ms() / 1000));
            ms.getStyleClass().add("trace-ms");
            HBox row = new HBox(8, name, bar, ms);
            row.setAlignment(Pos.CENTER_LEFT);
            if (entry.model_calls() > 0) {
                row.getChildren().add(muted(entry.model_calls()
                        + (entry.model_calls() == 1 ? " model call" : " model calls")));
            }
            rows.getChildren().add(row);
        }
        return rows;
    }

    private void wireFeedback() {
        feedback.setOnVote(verdict -> {
            // Clicking the verdict already recorded withdraws it.
            if (store.get(job.id()).filter(record -> record.verdict() == verdict).isPresent()) {
                store.withdraw(job.id());
            } else {
                store.vote(job.id(), job.question(), verdict);
            }
        });
        feedback.setOnComment(comment -> store.get(job.id()).ifPresent(record ->
                store.comment(job.id(), job.question(), record.verdict(), comment)));
        feedback.setOnRetry(() -> store.retry(job.id()));
        refreshFeedback();
    }

    private static TitledPane disclosure(String summary, Region body) {
        TitledPane pane = new TitledPane(summary, body);
        pane.getStyleClass().add("disclosure");
        pane.setExpanded(false);
        pane.setAnimated(false);
        return pane;
    }

    private static Region sql(String statement) {
        TextArea area = new TextArea(statement);
        area.getStyleClass().add("sql");
        area.setEditable(false);
        area.setWrapText(true);
        area.setPrefRowCount(Math.min(14, statement.split("\n", -1).length + 1));
        return area;
    }

    private static Label muted(String text) {
        Label label = new Label(text);
        label.getStyleClass().add("muted");
        label.setWrapText(true);
        return label;
    }
}
