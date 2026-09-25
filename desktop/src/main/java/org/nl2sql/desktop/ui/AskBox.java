package org.nl2sql.desktop.ui;

import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.TextArea;
import javafx.scene.input.KeyCode;
import javafx.scene.layout.FlowPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;

import java.util.List;
import java.util.function.Consumer;

/**
 * Where the question goes in.
 *
 * <p>A text area rather than a single-line field because the questions that
 * get good answers are sentences, and a one-line box tells the user to type
 * three words. Enter submits and shift-Enter adds a line, which is what every
 * other message box does.
 */
public final class AskBox {

    /**
     * Four questions from the benchmark suite, chosen to land on four
     * different chart shapes -- one number, a bar, a line and a grouped bar --
     * so the first thing a new user clicks shows what the application can do
     * rather than the same table four times.
     */
    public static final List<String> EXAMPLES = List.of(
            "How many stores are there?",
            "How many stores does each banner operate?",
            "What were our net sales by state in fiscal year 2025?",
            "Which 5 products had the highest net sales in fiscal year 2025? "
                    + "Give the SKU and the amount.");

    private final TextArea field = new TextArea();
    private final Button action = new Button("Ask");
    private final FlowPane examples = new FlowPane();
    private final Label counter = new Label();
    private final VBox node = new VBox();

    private Consumer<String> onAsk = question -> {
    };
    private Runnable onCancel = () -> {
    };
    private boolean busy;
    private int maxLength = 2000;

    public AskBox() {
        Label label = new Label("Ask the retail database a question");
        label.getStyleClass().add("ask-label");

        field.setPromptText("What were our total net sales in fiscal year 2025?");
        field.setPrefRowCount(2);
        field.setWrapText(true);
        field.getStyleClass().add("ask-input");
        field.textProperty().addListener((observable, before, after) -> textChanged(after));
        field.setOnKeyPressed(event -> {
            if (event.getCode() == KeyCode.ENTER && !event.isShiftDown()) {
                // Consumed before the area inserts it; otherwise the question
                // is submitted and a newline is left behind in the box.
                event.consume();
                submit();
            }
        });

        action.getStyleClass().addAll("button", "button-primary");
        action.setDefaultButton(true);
        action.setDisable(true);
        action.setOnAction(event -> {
            if (busy) {
                onCancel.run();
            } else {
                submit();
            }
        });

        counter.getStyleClass().add("muted");
        HBox row = new HBox(8, field, action);
        HBox.setHgrow(field, Priority.ALWAYS);
        row.getStyleClass().add("ask-row");

        examples.getStyleClass().add("ask-examples");
        examples.setHgap(6);
        examples.setVgap(6);
        Label tryThese = new Label("Try");
        tryThese.getStyleClass().add("ask-examples-label");
        examples.getChildren().add(tryThese);
        for (String example : EXAMPLES) {
            Button chip = new Button(example);
            chip.getStyleClass().add("chip");
            chip.setOnAction(event -> onAsk.accept(example));
            examples.getChildren().add(chip);
        }

        node.getStyleClass().add("ask");
        node.setSpacing(8);
        node.getChildren().addAll(label, row, counter, examples);
        counter.setManaged(false);
        counter.setVisible(false);
    }

    public Region node() {
        return node;
    }

    public void setOnAsk(Consumer<String> handler) {
        this.onAsk = handler;
    }

    public void setOnCancel(Runnable handler) {
        this.onCancel = handler;
    }

    /**
     * How long a question this server will take.
     *
     * <p>Read from {@code /v1/meta}, so the box refuses an over-long question
     * rather than the user discovering the rule from a 422 after pressing
     * Enter. Before meta lands the documented default stands, which is the
     * only value it has ever had.
     */
    public void setMaxQuestionLength(int characters) {
        this.maxLength = characters;
        textChanged(field.getText());
    }

    /** Ask/Stop, and whether the box can be typed in. */
    public void setBusy(boolean working) {
        this.busy = working;
        field.setDisable(working);
        action.setText(working ? "Stop" : "Ask");
        action.setDisable(working ? false : field.getText().isBlank());
        action.getStyleClass().removeAll("button-primary", "button-quiet");
        action.getStyleClass().add(working ? "button-quiet" : "button-primary");
    }

    /** Examples are for an empty page; they go once anything has been asked. */
    public void setExamplesVisible(boolean visible) {
        examples.setVisible(visible);
        examples.setManaged(visible);
    }

    /** For the tests and for the example chips: put a question in the box. */
    public void setQuestion(String question) {
        field.setText(question);
    }

    public String question() {
        return field.getText();
    }

    /** The Ask button, exposed so a test can press what a user presses. */
    public Button action() {
        return action;
    }

    public TextArea field() {
        return field;
    }

    /** Send what is in the box, if there is anything and nothing is running. */
    public void submit() {
        String question = field.getText();
        if (busy || question.isBlank() || question.length() > maxLength) {
            return;
        }
        onAsk.accept(question);
        // Cleared on send, like every other message box. Leaving it would mean
        // the next thing typed is appended to the last question rather than
        // replacing it, and the question itself is not lost -- it heads the
        // answer and stays in the session list.
        field.clear();
    }

    private void textChanged(String text) {
        boolean tooLong = text.length() > maxLength;
        counter.setText(tooLong
                ? text.length() + " characters; this server takes " + maxLength
                : "");
        counter.setManaged(tooLong);
        counter.setVisible(tooLong);
        action.setDisable(busy ? false : text.isBlank() || tooLong);
    }
}
