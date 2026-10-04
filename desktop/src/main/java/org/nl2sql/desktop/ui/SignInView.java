package org.nl2sql.desktop.ui;

import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.PasswordField;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;

import java.net.URI;
import java.util.function.BiConsumer;

/**
 * Who you are, asked for when the server wants to know.
 *
 * <p>A panel above the question box rather than a dialog. A modal window
 * would block the one thing a person stuck at sign-in wants to do, which is
 * read the error under the last question; and a dialog that opens on top of
 * a window the user is already looking at is exactly the shape a phishing
 * prompt takes. This one says where the password is going.
 *
 * <p>The password field is emptied after every attempt, successful or not:
 * a password left in a field is a password a screenshot can read.
 */
public final class SignInView {

    private final VBox node = new VBox();
    private final Label reason = new Label();
    private final TextField user = new TextField();
    private final PasswordField password = new PasswordField();
    private final Button action = new Button("Sign in");
    private final Label problem = new Label();

    private BiConsumer<String, String> onSignIn = (name, secret) -> {
    };

    /**
     * @param authUrl where the password is sent, said on the panel
     * @param user    the user name to offer, or empty
     */
    public SignInView(URI authUrl, String user) {
        Label title = new Label("Sign in");
        title.getStyleClass().add("signin-title");
        reason.getStyleClass().add("muted");
        reason.setWrapText(true);
        reason.setMinWidth(0);

        this.user.setText(user);
        this.user.setPromptText("user name");
        password.setPromptText("password");
        this.user.setOnAction(event -> password.requestFocus());
        password.setOnAction(event -> submit());

        action.getStyleClass().addAll("button", "button-primary");
        action.setMinWidth(Region.USE_PREF_SIZE);
        action.setOnAction(event -> submit());

        HBox row = new HBox(8, this.user, password, action);
        HBox.setHgrow(this.user, Priority.ALWAYS);
        HBox.setHgrow(password, Priority.ALWAYS);
        this.user.setMinWidth(0);
        password.setMinWidth(0);

        problem.getStyleClass().add("feedback-error");
        problem.setWrapText(true);
        problem.setMinWidth(0);
        problem.setVisible(false);
        problem.setManaged(false);

        Label where = new Label(where(authUrl));
        where.getStyleClass().add("muted");
        where.setWrapText(true);
        where.setMinWidth(0);

        node.getStyleClass().addAll("notice", "signin");
        node.setSpacing(8);
        node.setMinWidth(0);
        node.getChildren().addAll(title, reason, row, problem, where);
    }

    public Region node() {
        return node;
    }

    public void setOnSignIn(BiConsumer<String, String> handler) {
        this.onSignIn = handler;
    }

    /** Why the panel is showing, in the server's words or the window's. */
    public void setReason(String text) {
        reason.setText(text);
    }

    /** What went wrong with the last attempt; the password is already gone. */
    public void showProblem(String text) {
        problem.setText(text);
        problem.setVisible(true);
        problem.setManaged(true);
    }

    /** Signing in is under way: nothing here can be pressed twice. */
    public void setBusy(boolean working) {
        user.setDisable(working);
        password.setDisable(working);
        action.setDisable(working);
        action.setText(working ? "Signing in…" : "Sign in");
    }

    /** Send what is in the fields, unless one of them is empty. */
    public void submit() {
        String name = user.getText().trim();
        String secret = password.getText();
        password.clear();
        if (name.isEmpty() || secret.isEmpty()) {
            showProblem("Enter your user name and your password.");
            return;
        }
        problem.setVisible(false);
        problem.setManaged(false);
        onSignIn.accept(name, secret);
    }

    // --- for the tests, which type what a user types ------------------------

    public TextField user() {
        return user;
    }

    public PasswordField password() {
        return password;
    }

    public Button action() {
        return action;
    }

    public Label problem() {
        return problem;
    }

    /**
     * Where the password goes, in one line.
     *
     * <p>Plain HTTP is said out loud: a deployment can turn TLS off behind
     * something that terminates it, and a user typing a password into a
     * window has a right to know when nothing between here and there does.
     */
    static String where(URI authUrl) {
        String host = authUrl.getAuthority();
        return authUrl.getScheme().equals("https")
                ? "Your password goes to " + host + " and nowhere else; the window keeps the "
                        + "session it gets back, in memory, until it closes."
                : "Your password goes to " + host + " UNENCRYPTED. Use this only on a "
                        + "network you trust, or ask for the https address.";
    }
}
