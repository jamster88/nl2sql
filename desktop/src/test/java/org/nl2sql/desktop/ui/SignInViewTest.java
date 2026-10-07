package org.nl2sql.desktop.ui;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;

import java.net.URI;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

@ExtendWith(FxToolkit.class)
class SignInViewTest {

    private final List<String> sent = new ArrayList<>();

    private SignInView view(String url, String user) {
        SignInView view = new SignInView(URI.create(url), user);
        view.setOnSignIn((name, password) -> sent.add(name + ":" + password));
        return view;
    }

    @Test
    void the_user_name_it_was_told_is_offered_and_the_password_goes_where_it_says() {
        FxToolkit.onFx(() -> {
            SignInView view = view("https://nl2sql.example.com:8446", "ada");
            view.setReason("This server asks who you are.");

            assertEquals("ada", view.user().getText());
            assertTrue(Nodes.says(view.node(), "This server asks who you are."));
            assertTrue(Nodes.says(view.node(), "Your password goes to nl2sql.example.com:8446 and nowhere else"));
        });
    }

    @Test
    void a_plain_http_sign_in_says_so() {
        assertTrue(SignInView.where(URI.create("http://intranet:8446"))
                .contains("intranet:8446 UNENCRYPTED"));
    }

    @Test
    void signing_in_sends_both_and_forgets_the_password() {
        FxToolkit.onFx(() -> {
            SignInView view = view("https://localhost:8446", "");
            view.user().setText("  ada ");
            // Enter in the user name moves on to the password rather than
            // sending half a sign-in.
            view.user().getOnAction().handle(null);
            assertEquals(List.of(), sent);
            view.password().setText("correct horse");

            view.password().getOnAction().handle(null);

            assertEquals(List.of("ada:correct horse"), sent);
            assertEquals("", view.password().getText());
            assertFalse(view.problem().isVisible());
        });
    }

    @Test
    void an_empty_field_is_refused_here_rather_than_by_the_server() {
        FxToolkit.onFx(() -> {
            SignInView view = view("https://localhost:8446", "ada");

            view.action().fire();

            assertEquals(List.of(), sent);
            assertTrue(view.problem().isVisible());
            assertEquals("Enter your user name and your password.", view.problem().getText());

            view.user().setText("");
            view.password().setText("pw");
            view.submit();

            assertEquals(List.of(), sent);
            assertEquals("", view.password().getText());
        });
    }

    @Test
    void while_signing_in_nothing_can_be_pressed_twice() {
        FxToolkit.onFx(() -> {
            SignInView view = view("https://localhost:8446", "ada");

            view.setBusy(true);

            assertTrue(view.action().isDisabled() && view.user().isDisabled() && view.password().isDisabled());
            assertEquals("Signing in…", view.action().getText());

            view.setBusy(false);
            view.showProblem("that user name and password were not accepted");

            assertFalse(view.action().isDisabled());
            assertEquals("Sign in", view.action().getText());
            assertTrue(view.problem().isVisible());
        });
    }

    @Test
    void a_panel_nobody_is_listening_to_does_nothing_rather_than_failing() {
        FxToolkit.onFx(() -> {
            SignInView view = new SignInView(URI.create("https://localhost:8446"), "ada");
            view.password().setText("pw");

            view.submit();

            assertEquals("", view.password().getText());
            assertFalse(view.problem().isVisible());
        });
    }
}
