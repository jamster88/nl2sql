package org.nl2sql.desktop.api;

import com.sun.net.httpserver.HttpsConfigurator;
import com.sun.net.httpserver.HttpsServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.nl2sql.desktop.Fakes;
import org.nl2sql.desktop.Settings;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.security.KeyStore;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Signing in against a real socket, over the TLS the API uses.
 *
 * <p>One server plays both parts -- {@code /auth/token} and {@code /v1/meta}
 * -- because the thing worth proving is the hand-off: the token signing in
 * returns is the one the API client presents next.
 */
class HttpSignInTest {

    private HttpsServer server;
    // Written by the server's thread; read by the test's, sign-out's request
    // arriving whenever it does.
    private final List<String> requested = new CopyOnWriteArrayList<>();
    private final List<String> bodies = new CopyOnWriteArrayList<>();
    private final List<String> authorisation = new CopyOnWriteArrayList<>();

    @AfterEach
    void stopServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    private Settings serve(int status, String answer) {
        try {
            server = HttpsServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.setHttpsConfigurator(new HttpsConfigurator(serverContext()));
            server.createContext("/", exchange -> {
                requested.add(exchange.getRequestMethod() + " " + exchange.getRequestURI().getPath());
                String token = exchange.getRequestHeaders().getFirst("Authorization");
                authorisation.add(token == null ? "" : token);
                try (InputStream incoming = exchange.getRequestBody()) {
                    bodies.add(new String(incoming.readAllBytes(), StandardCharsets.UTF_8));
                }
                boolean signingIn = exchange.getRequestURI().getPath().equals("/auth/token");
                byte[] bytes = (signingIn ? answer : Json.write(Fakes.meta(true, "session")))
                        .getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(signingIn ? status : 200, bytes.length);
                try (OutputStream out = exchange.getResponseBody()) {
                    out.write(bytes);
                }
            });
            server.start();
        } catch (IOException cause) {
            throw new IllegalStateException(cause);
        }
        String base = "https://localhost:" + server.getAddress().getPort();
        return Settings.from(Map.of(), "--url", base, "--auth-url", base,
                "--cacert", TestCertificate.pem().toString());
    }

    private static SSLContext serverContext() {
        try {
            KeyStore store = KeyStore.getInstance("PKCS12");
            try (InputStream in = Files.newInputStream(TestCertificate.keystore())) {
                store.load(in, TestCertificate.PASSWORD.toCharArray());
            }
            KeyManagerFactory keys =
                    KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
            keys.init(store, TestCertificate.PASSWORD.toCharArray());
            SSLContext context = SSLContext.getInstance("TLS");
            context.init(keys.getKeyManagers(), null, null);
            return context;
        } catch (Exception cause) {
            throw new IllegalStateException("cannot start a TLS test server", cause);
        }
    }

    @Test
    void the_session_signing_in_returns_is_what_the_api_client_presents_next() {
        Settings settings = serve(200, Json.write(Fakes.token("ada", "Ada Lovelace")));
        Session session = new Session("");
        HttpApiClient api = new HttpApiClient(settings, session);
        HttpSignIn signIn = new HttpSignIn(settings, session);

        api.meta();
        Models.Token token = signIn.signIn("ada", "correct horse");
        api.meta();

        assertEquals("Ada Lovelace (ada)", token.who());
        assertSame(token, session.current());
        assertEquals(List.of("GET /v1/meta", "POST /auth/token", "GET /v1/meta"), requested);
        assertEquals(Json.write(new Models.SignIn("ada", "correct horse")), bodies.get(1));
        assertEquals(List.of("", "", "Bearer eyJ.session.ada"), authorisation);

        signIn.signOut();
        api.meta();

        assertNull(session.current());
        assertTrue(authorisation.stream().skip(3).anyMatch(String::isEmpty),
                "the API is asked with nothing once signed out");
        awaitRequest("POST /auth/logout");
        int logout = requested.indexOf("POST /auth/logout");
        assertEquals("Bearer eyJ.session.ada", authorisation.get(logout),
                "the session is ended at the auth service, by itself");
    }

    @Test
    void signing_out_with_nobody_signed_in_tells_nobody() throws InterruptedException {
        Settings settings = serve(200, Json.write(Fakes.token("ada", "Ada Lovelace")));
        Session session = new Session("a-service-token");
        new HttpSignIn(settings, session).signOut();
        Thread.sleep(200);
        assertEquals(List.of(), requested);
        assertEquals("a-service-token", session.bearer());
    }

    private void awaitRequest(String wanted) {
        long deadline = System.nanoTime() + 10_000_000_000L;
        while (!requested.contains(wanted)) {
            if (System.nanoTime() > deadline) {
                throw new AssertionError("never asked: " + wanted + " (asked: " + requested + ")");
            }
            try {
                Thread.sleep(10);
            } catch (InterruptedException cause) {
                Thread.currentThread().interrupt();
                throw new AssertionError("interrupted waiting for " + wanted);
            }
        }
    }

    @Test
    void a_refusal_is_the_services_own_words() {
        Settings settings = serve(401, "{\"error\": {\"code\": \"invalid_credentials\", "
                + "\"message\": \"that user name and password were not accepted\"}}");
        Session session = new Session("static");

        ApiException refused = assertThrows(ApiException.class,
                () -> new HttpSignIn(settings, session).signIn("ada", "wrong"));

        assertEquals(401, refused.status());
        assertEquals("invalid_credentials", refused.code());
        assertEquals("that user name and password were not accepted", refused.getMessage());
        assertEquals("static", session.bearer());
    }

    @Test
    void an_answer_without_a_session_in_it_is_not_a_sign_in() {
        Settings settings = serve(200, "{}");
        Session session = new Session("");

        ApiException empty = assertThrows(ApiException.class,
                () -> new HttpSignIn(settings, session).signIn("ada", "pw"));

        assertEquals(502, empty.status());
        assertTrue(empty.getMessage().contains("answered without a session"), empty.getMessage());
        assertNull(session.current());
    }

    @Test
    void a_service_that_is_not_there_is_named() {
        Settings settings = Settings.from(Map.of(), "--auth-url", "https://localhost:1", "--insecure");

        ApiException failed = assertThrows(ApiException.class,
                () -> new HttpSignIn(settings, new Session("")).signIn("ada", "pw"));

        assertEquals("unreachable", failed.code());
        assertTrue(failed.getMessage().startsWith(
                "cannot reach the sign-in service at https://localhost:1: "), failed.getMessage());
    }

    @Test
    void signing_in_on_an_interrupted_thread_reports_it_and_leaves_the_flag() throws Exception {
        Settings settings = serve(200, Json.write(Fakes.token("ada", "")));
        HttpSignIn signIn = new HttpSignIn(settings, new Session(""));
        List<String> seen = new ArrayList<>();
        Thread caller = new Thread(() -> {
            Thread.currentThread().interrupt();
            try {
                signIn.signIn("ada", "pw");
                seen.add("signed in");
            } catch (ApiException failure) {
                seen.add(failure.code());
            }
            seen.add("interrupted=" + Thread.currentThread().isInterrupted());
        });
        caller.start();
        caller.join(20_000);

        assertEquals(List.of("interrupted", "interrupted=true"), seen);
    }
}
