package org.nl2sql.desktop.api;

import org.nl2sql.desktop.Settings;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

/**
 * {@code POST /auth/token} on the auth service.
 *
 * <p>The route for a client that holds its own token. A browser signs in at
 * {@code /auth/login} and is handed a cookie it cannot read; this client has
 * no cookie jar worth the name, and sends the token it is given as a bearer
 * header -- which is also what the API does with a service token, so the
 * server needs nothing new to accept it.
 *
 * <p>Over the same TLS settings as the API. The auth service presents a
 * certificate of its own, issued by the same CA as the API's: one
 * {@code --cacert} with that CA covers both, and so does a
 * {@code --fingerprint} that names both.
 */
public final class HttpSignIn implements SignIn {

    private final Settings settings;
    private final Session session;
    private final HttpClient http;

    public HttpSignIn(Settings settings, Session session) {
        this.settings = settings;
        this.session = session;
        this.http = Tls.client(settings);
    }

    @Override
    public Models.Token signIn(String username, String password) {
        String body = Json.write(new Models.SignIn(username, password));
        HttpRequest request = HttpRequest.newBuilder(URI.create(settings.authUrl() + "/auth/token"))
                .header("Accept", "application/json")
                .header("Content-Type", "application/json")
                .timeout(Duration.ofSeconds(30))
                .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                .build();
        HttpResponse<String> response;
        try {
            response = http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (IOException cause) {
            throw new ApiException(0, "unreachable",
                    "cannot reach the sign-in service at " + settings.authUrl() + ": "
                            + HttpApiClient.reason(cause));
        } catch (InterruptedException cause) {
            Thread.currentThread().interrupt();
            throw new ApiException(0, "interrupted", "signing in was interrupted");
        }
        if (response.statusCode() >= 300) {
            throw HttpApiClient.described(response.statusCode(), response.body());
        }
        Models.Token token = Json.read(response.body(), Models.Token.class);
        // A proxy that answers 200 with an empty page would otherwise leave
        // the window believing it had signed in, and the next question would
        // be a 401 that seemed to come from nowhere.
        if (token.token().isEmpty()) {
            throw new ApiException(502, "error",
                    "the sign-in service at " + settings.authUrl() + " answered without a session");
        }
        session.signedIn(token);
        return token;
    }

    @Override
    public void signOut() {
        session.signOut();
    }
}
