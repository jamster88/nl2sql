package org.nl2sql.desktop.ui;

import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.TextField;
import javafx.geometry.Pos;
import javafx.scene.layout.FlowPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import org.nl2sql.desktop.api.Models;
import org.nl2sql.desktop.feedback.FeedbackRecord;
import org.nl2sql.desktop.feedback.SyncState;

import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.time.format.FormatStyle;
import java.util.function.Consumer;

/**
 * Was that right?
 *
 * <p>Two buttons, and -- once one is pressed -- somewhere to say why. The
 * ordering is deliberate: the verdict is one click and is recorded on that
 * click, and the comment box only appears afterwards. Asking for prose up
 * front turns a one-click courtesy into a form, and a feedback widget that
 * looks like a form is one people scroll past.
 *
 * <p>Clicking the verdict already recorded withdraws it. A user who misclicks
 * "No" on a good answer should not have to hunt for an undo, and a verdict
 * that cannot be taken back is a verdict people stop giving.
 *
 * <p>The send state is shown rather than hidden. A verdict that failed to
 * reach the server is the one case where saying nothing actively misleads:
 * the user believes they have reported the problem, and nobody has heard it.
 *
 * <p>Nothing here knows where a verdict goes. It calls handlers it was given,
 * which is how the same component serves a server that stages feedback and
 * one that has never heard of it.
 */
public final class FeedbackBar {

    private final Label prompt = new Label();
    private final Button yes = new Button("Yes");
    private final Button no = new Button("No");
    private final Label note = new Label();
    private final Button retry = new Button("Retry");
    private final Label failure = new Label();
    private final Label commentLabel = new Label();
    private final TextField comment = new TextField();
    private final Button send = new Button("Send");
    private final VBox commentForm = new VBox();
    private final VBox node = new VBox();

    private Consumer<Models.Verdict> onVote = verdict -> {
    };
    private Consumer<String> onComment = text -> {
    };
    private Runnable onRetry = () -> {
    };

    private boolean commentable = true;
    private boolean commentSent;
    private Models.Verdict recorded;

    public FeedbackBar() {
        prompt.getStyleClass().add("feedback-prompt");
        yes.getStyleClass().addAll("button", "button-vote");
        no.getStyleClass().addAll("button", "button-vote");
        yes.setOnAction(event -> onVote.accept(Models.Verdict.YES));
        no.setOnAction(event -> onVote.accept(Models.Verdict.NO));

        note.getStyleClass().addAll("feedback-note", "muted");
        note.setWrapText(true);
        retry.getStyleClass().addAll("button", "button-quiet");
        retry.setOnAction(event -> onRetry.run());
        failure.getStyleClass().add("feedback-error");
        failure.setWrapText(true);

        // A FlowPane, not an HBox: the prompt, two buttons, a timestamp and
        // a Retry do not fit on one line of a narrow window, and an HBox
        // would push the last of them off the end rather than below.
        FlowPane row = new FlowPane(8, 4, prompt, yes, no, note, retry);
        row.setAlignment(Pos.CENTER_LEFT);
        row.getStyleClass().add("feedback-row");

        commentLabel.getStyleClass().add("feedback-comment-label");
        comment.getStyleClass().add("input");
        comment.setOnAction(event -> sendComment());
        send.getStyleClass().add("button");
        send.setOnAction(event -> sendComment());
        send.setDisable(true);
        comment.textProperty().addListener(
                (observable, before, after) -> send.setDisable(after.isBlank()));
        HBox commentRow = new HBox(8, comment, send);
        HBox.setHgrow(comment, Priority.ALWAYS);
        commentForm.getStyleClass().add("feedback-comment");
        commentForm.setSpacing(4);
        commentForm.getChildren().addAll(commentLabel, commentRow);

        node.getStyleClass().add("feedback");
        node.setSpacing(6);
        node.getChildren().addAll(row, failure, commentForm);
        show(null);
    }

    public Region node() {
        return node;
    }

    public void setOnVote(Consumer<Models.Verdict> handler) {
        this.onVote = handler;
    }

    /**
     * Where a comment goes.
     *
     * <p>{@code false} for a server that stages nothing: the box is then not
     * drawn at all, because a comment with nowhere to go is a lie to the user.
     */
    public void setCommentable(boolean canComment) {
        this.commentable = canComment;
        layout();
    }

    public void setOnComment(Consumer<String> handler) {
        this.onComment = handler;
    }

    public void setOnRetry(Runnable handler) {
        this.onRetry = handler;
    }

    /** Draw the verdict, or the invitation to give one when there is none. */
    public void show(FeedbackRecord record) {
        Models.Verdict was = recorded;
        recorded = record == null ? null : record.verdict();
        if (was != recorded) {
            // A new verdict is a new opinion, so the comment box starts empty
            // and open again rather than still showing the last one's note.
            comment.clear();
            commentSent = false;
        }

        prompt.setText(record == null ? "Was this answer right?" : "Thanks — recorded as");
        yes.pseudoClassStateChanged(Styles.CHOSEN, recorded == Models.Verdict.YES);
        no.pseudoClassStateChanged(Styles.CHOSEN, recorded == Models.Verdict.NO);

        SyncState state = record == null ? SyncState.LOCAL : record.sync();
        note.setText(record == null ? "" : at(record.at()) + state.note());
        failure.setText(record == null ? "" : record.error());

        boolean failed = record != null && state == SyncState.FAILED;
        visible(note, record != null);
        visible(retry, failed);
        visible(failure, failed && !record.error().isEmpty());

        if (record != null) {
            commentLabel.setText(record.verdict() == Models.Verdict.NO
                    ? "What was wrong? (optional)"
                    : "Anything worth noting? (optional)");
            comment.setPromptText(record.verdict() == Models.Verdict.NO
                    ? "e.g. the fiscal month is off by one"
                    : "e.g. worth keeping as an example");
        }
        layout();
    }

    /** The two buttons and the comment field, for tests that press them. */
    public Button yes() {
        return yes;
    }

    public Button no() {
        return no;
    }

    public Button retry() {
        return retry;
    }

    public TextField comment() {
        return comment;
    }

    public Button send() {
        return send;
    }

    public Label note() {
        return note;
    }

    private void sendComment() {
        String text = comment.getText().trim();
        if (text.isEmpty()) {
            return;
        }
        onComment.accept(text);
        commentSent = true;
        layout();
    }

    private void layout() {
        boolean wanted = recorded != null && commentable && !commentSent;
        visible(commentForm, wanted);
        if (commentSent) {
            note.setText("Comment sent. Thank you.");
            visible(note, true);
        }
    }

    /**
     * When the verdict was given, in this machine's own words.
     *
     * <p>An unparseable timestamp is dropped rather than shown: it came from a
     * file a previous version wrote, and "1970" beside a verdict given a
     * minute ago is worse than no time at all.
     */
    private static String at(String iso) {
        try {
            return DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT)
                    .format(Instant.parse(iso).atZone(ZoneId.systemDefault())) + " · ";
        } catch (DateTimeParseException unreadable) {
            return "";
        }
    }

    private static void visible(javafx.scene.Node target, boolean showing) {
        target.setVisible(showing);
        target.setManaged(showing);
    }
}
