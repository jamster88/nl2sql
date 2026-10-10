package org.nl2sql.desktop.ui;

import javafx.scene.control.Label;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import org.nl2sql.desktop.api.Models;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * The question, asked several ways (7.0).
 *
 * <p>An answer under the ensemble is not one run's: the question was reworded,
 * every wording was run through the whole pipeline, a Judge read the answers,
 * and the runs it left standing voted. The sentence at the top of the answer
 * is what came out of that. This is what went into it, for the reader who
 * wants to know why they should believe "3 of 4 runs agreed" -- or why they
 * should not.
 *
 * <p>Three parts, which {@link AnswerView} places:
 *
 * <ul>
 *   <li><b>The agreement line</b>, shown with the sentence: the level in a
 *       word, the count, and the agent's own sentence about the vote. The
 *       sentence is the server's ({@code Ensemble.line}) rather than composed
 *       here, so this window, the web interface and the terminal say the same
 *       thing.
 *   <li><b>The notes</b>: each answer that lost, with how its query differs;
 *       each column joined on from another run that agreed, since the query
 *       shown does not return it; and each column that would not join.
 *   <li><b>The runs</b>, folded away: every wording, what was changed to make
 *       it, how its run ended, which answer it gave and its SQL; what the
 *       Judge said of each answer; and the rewordings that were never run.
 * </ul>
 *
 * <p>Everything here that a model wrote -- a rewording, what it says it
 * changed, the Judge's reason -- is drawn by a label or a text area, which
 * draw a string as text. None of it is unescaped either: unlike
 * {@code answer}, these fields were never escaped for markdown.
 */
final class EnsembleView {

    /** The vote's level, in a word or two, for the badge. */
    static final Map<String, String> LEVEL_LABELS = Map.of(
            "unanimous", "All agree",
            "majority", "Most agree",
            "judged", "The Judge's choice",
            "contested", "Contested",
            "single", "One answer",
            "none", "No answer");

    /** How a run ended, as the runs list says it. */
    static final Map<String, String> OUTCOME_LABELS = Map.of(
            "answered", "answered",
            "gave_up", "gave up",
            "refused", "refused");

    private EnsembleView() {
    }

    /** An answer's letter: the first group is A, as the Judge was shown them. */
    static String letter(int group) {
        return String.valueOf((char) ('A' + group));
    }

    /** "Run 2", "Runs 1 and 3", "Runs 1, 2 and 3". */
    static String runsNamed(List<Integer> members) {
        if (members.size() == 1) {
            return "Run " + members.get(0);
        }
        List<String> names = new ArrayList<>();
        for (Integer member : members.subList(0, members.size() - 1)) {
            names.add(String.valueOf(member));
        }
        return "Runs " + String.join(", ", names) + " and " + members.get(members.size() - 1);
    }

    /** The agreement line, as a badge with the sentence. */
    static Region badge(Models.Ensemble ensemble, Region column) {
        Models.EnsembleAgreement agreement = ensemble.agreement();
        Label badge = new Label(LEVEL_LABELS.getOrDefault(agreement.level(), agreement.level())
                + " · " + agreement.agreed() + " of " + agreement.total());
        // The level is said in the badge's words; the colour only repeats it.
        badge.getStyleClass().addAll("agreement-badge", "agreement-" + agreement.level());
        VBox box = new VBox(4, badge);
        box.getStyleClass().add("agreement");
        String line = ensemble.line().isEmpty() ? agreement.why() : ensemble.line();
        if (!line.isEmpty()) {
            Label said = AnswerView.wrapTo(new Label(line), column, 32);
            said.getStyleClass().addAll("agreement-line", "muted");
            box.getChildren().add(said);
        }
        return box;
    }

    /** What the vote left out or brought in; null when there is nothing to say. */
    static Region notes(Models.Ensemble ensemble, Region column) {
        List<String> notes = new ArrayList<>();
        for (Models.JoinedColumn joined : ensemble.joined_columns()) {
            notes.add(joined.column() + " is from run " + joined.from_candidate()
                    + ", which agreed, joined on " + joined.key()
                    + "; this answer's query does not return it.");
        }
        for (Models.DeclinedColumn declined : ensemble.declined_columns()) {
            notes.add(declined.column() + ", which run " + declined.from_candidate()
                    + " also returned, was left out: " + declined.why() + ".");
        }
        for (Models.EnsembleDissent dissent : ensemble.dissent()) {
            notes.add(runsNamed(dissent.members()) + " answered differently ("
                    + dissent.signature() + ")"
                    + (dissent.differs().isEmpty() ? "" : ": " + dissent.differs()) + ".");
        }
        if (notes.isEmpty()) {
            return null;
        }
        VBox box = new VBox(4);
        box.getStyleClass().add("ensemble-notes");
        for (String note : notes) {
            box.getChildren().add(AnswerView.wrapTo(new Label("•  " + note), column, 32));
        }
        return box;
    }

    /** The folded pane's title: how many ways the question was asked. */
    static String title(Models.Ensemble ensemble) {
        int ways = ensemble.candidates().size();
        return "The question, asked " + ways + (ways == 1 ? " way" : " ways");
    }

    /** Every wording and its run, the Judge, and the rewordings not run. */
    static Region runs(Models.Ensemble ensemble) {
        VBox body = new VBox(6);
        body.getStyleClass().add("candidates");
        for (Models.EnsembleCandidate candidate : ensemble.candidates()) {
            Label wording = AnswerView.wrapTo(
                    new Label(candidate.index() + "   " + candidate.wording()), body, 8);
            wording.getStyleClass().add("candidate-wording");
            if (candidate.index() == chosen(ensemble)) {
                wording.getStyleClass().add("chosen");
            }
            body.getChildren().addAll(wording,
                    AnswerView.wrapTo(AnswerView.muted(standing(candidate, ensemble)), body, 8));
            for (String reason : candidate.reasons()) {
                Label why = AnswerView.wrapTo(new Label(reason), body, 8);
                why.getStyleClass().add("candidate-reason");
                body.getChildren().add(why);
            }
            if (!candidate.sql().isEmpty()) {
                body.getChildren().add(AnswerView.sql(candidate.sql()));
            }
        }

        Models.EnsembleJudgement judged = ensemble.judged();
        if (judged != null) {
            body.getChildren().add(headline(
                    "The Judge" + (judged.model().isEmpty() ? "" : " · " + judged.model())));
            if (!judged.error().isEmpty()) {
                body.getChildren().add(AnswerView.wrapTo(new Label(
                        "It could not be asked, so the runs voted alone: " + judged.error()), body, 8));
            }
            for (Models.EnsembleVerdict verdict : judged.verdicts()) {
                body.getChildren().add(AnswerView.wrapTo(new Label("Answer " + letter(verdict.group())
                        + ": " + (verdict.accepted() ? "accepted" : "set aside")
                        + (verdict.why().isEmpty() ? "" : " — " + verdict.why())), body, 8));
            }
        }

        if (!ensemble.discarded().isEmpty()) {
            body.getChildren().add(headline("Rewordings that were not run"));
            for (Models.DiscardedRewording rewording : ensemble.discarded()) {
                body.getChildren().add(AnswerView.wrapTo(new Label(rewording.text()
                        + (rewording.changed().isEmpty() ? "" : " · changed: " + rewording.changed())
                        + " · " + rewording.reason()), body, 8));
            }
        }
        return body;
    }

    /** The delivered run's index, or one no run has when none was delivered. */
    private static int chosen(Models.Ensemble ensemble) {
        return ensemble.chosen() == null ? -1 : ensemble.chosen();
    }

    /** Where a run stands in the vote, as the words under its wording. */
    static String standing(Models.EnsembleCandidate candidate, Models.Ensemble ensemble) {
        List<String> tags = new ArrayList<>();
        tags.add(candidate.origin().equals("original")
                ? "As asked"
                : "Reworded" + (candidate.changed().isEmpty() ? "" : ": " + candidate.changed()));
        tags.add(OUTCOME_LABELS.getOrDefault(candidate.outcome(), candidate.outcome()));
        if (candidate.group() != null) {
            tags.add("answer " + letter(candidate.group()));
        }
        if (candidate.index() == chosen(ensemble)) {
            tags.add("delivered");
        } else if (ensemble.fused_from().contains(candidate.index())) {
            tags.add("agreed");
        }
        if (ensemble.judged() != null && ensemble.judged().set_aside().contains(candidate.index())) {
            tags.add("set aside by the Judge");
        }
        if (candidate.wave() > 1) {
            tags.add("wave " + candidate.wave());
        }
        if (!candidate.signature().isEmpty()) {
            tags.add("says " + candidate.signature());
        }
        if (candidate.attempts() > 1) {
            tags.add(candidate.attempts() + " attempts");
        }
        tags.add(String.format(Locale.US, "%.1fs", candidate.duration_ms() / 1000));
        return String.join(" · ", tags);
    }

    private static Label headline(String text) {
        Label label = new Label(text);
        label.getStyleClass().add("notice-headline");
        return label;
    }
}
