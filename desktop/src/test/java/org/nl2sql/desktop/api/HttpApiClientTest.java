package org.nl2sql.desktop.api;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
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
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The client, against a real socket.
 *
 * <p>Over real TLS, with the certificate the client is told to trust, for the
 * same reason {@code tests/api/test_live_tls.py} does it on the Python side:
 * the encrypted path is where a client's mistakes are invisible until
 * deployment, and a mocked transport proves nothing about a trust manager.
 */
class HttpApiClientTest {

    private HttpServer server;
    private final List<String> requested = new ArrayList<>();
    private final List<String> authorisation = new ArrayList<>();
    private final List<String> bodies = new ArrayList<>();

    @AfterEach
    void stopServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    /** A reply: a status and a body, chosen by path. */
    private interface Reply {
        String body(HttpExchange exchange);
    }

    private HttpApiClient https(Map<String, Integer> statuses, Reply reply, String... arguments) {
        return client(true, statuses, reply, arguments);
    }

    private HttpApiClient client(boolean tls, Map<String, Integer> statuses, Reply reply,
                                 String... arguments) {
        try {
            if (tls) {
                HttpsServer secure = HttpsServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
                secure.setHttpsConfigurator(new HttpsConfigurator(serverContext()));
                server = secure;
            } else {
                server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            }
            server.createContext("/", exchange -> {
                requested.add(exchange.getRequestMethod() + " " + exchange.getRequestURI());
                String token = exchange.getRequestHeaders().getFirst("Authorization");
                authorisation.add(token == null ? "" : token);
                try (InputStream incoming = exchange.getRequestBody()) {
                    bodies.add(new String(incoming.readAllBytes(), StandardCharsets.UTF_8));
                }
                String body = reply.body(exchange);
                int status = statuses.getOrDefault(exchange.getRequestURI().getPath(), 200);
                byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(status, bytes.length == 0 ? -1 : bytes.length);
                if (bytes.length > 0) {
                    try (OutputStream out = exchange.getResponseBody()) {
                        out.write(bytes);
                    }
                }
            });
            server.start();
        } catch (IOException cause) {
            throw new IllegalStateException(cause);
        }
        String base = (tls ? "https" : "http") + "://localhost:" + server.getAddress().getPort();
        List<String> all = new ArrayList<>(List.of("--url", base));
        all.addAll(List.of(arguments));
        if (tls && !all.contains("--insecure") && !all.contains("--fingerprint")) {
            all.addAll(List.of("--cacert", TestCertificate.pem().toString()));
        }
        Settings settings = Settings.from(Map.of(), all.toArray(new String[0]));
        return new HttpApiClient(settings);
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

    // --- the calls ---------------------------------------------------------

    @Test
    void meta_and_readiness_come_back_parsed_over_real_tls() {
        HttpApiClient client = https(Map.of(), exchange ->
                exchange.getRequestURI().getPath().equals("/v1/meta")
                        ? Json.write(Fakes.meta(true))
                        : Json.write(Fakes.ready()));

        assertEquals("4.5.0", client.meta().version());
        assertTrue(client.readiness().ready());
        assertEquals(List.of("GET /v1/meta", "GET /readyz"), requested);
    }

    @Test
    void a_token_is_presented_as_a_bearer_header() {
        HttpApiClient client = https(Map.of(), exchange -> Json.write(Fakes.meta(true)),
                "--token", "s3cret");

        client.meta();

        assertEquals(List.of("Bearer s3cret"), authorisation);
    }

    @Test
    void no_token_means_no_header_rather_than_an_empty_one() {
        // `Bearer` with nothing after it is a 401 that looks like a server
        // problem rather than a client one.
        https(Map.of(), exchange -> Json.write(Fakes.meta(true))).meta();

        assertEquals(List.of(""), authorisation);
    }

    @Test
    void asking_posts_the_question_and_waits_for_as_long_as_it_was_told_to() {
        HttpApiClient client = https(Map.of("/v1/questions", 202),
                exchange -> Json.write(Fakes.queued()), "--wait", "30");

        assertEquals("job-1", client.ask("how many stores", 30).id());
        assertEquals(List.of("POST /v1/questions?wait=30"), requested);
        assertEquals("{\"question\":\"how many stores\",\"principal\":null,\"metadata\":{}}",
                bodies.get(0));
    }

    @Test
    void asking_without_a_wait_sends_no_wait_at_all() {
        HttpApiClient client = https(Map.of(), exchange -> Json.write(Fakes.queued()));

        client.ask("how many stores", 0);

        assertEquals(List.of("POST /v1/questions"), requested);
    }

    @Test
    void an_id_with_something_awkward_in_it_is_escaped() {
        HttpApiClient client = https(Map.of(), exchange -> Json.write(Fakes.answered()));

        client.job("a b/c");

        assertEquals(List.of("GET /v1/questions/a+b%2Fc"), requested);
    }

    @Test
    void the_job_list_takes_a_limit() {
        HttpApiClient client = https(Map.of(),
                exchange -> Json.write(new Models.JobList(List.of(Fakes.answered()), 1)));

        assertEquals(1, client.jobs(25).count());
        assertEquals(List.of("GET /v1/questions?limit=25"), requested);
    }

    @Test
    void cancelling_and_withdrawing_send_a_delete_and_read_nothing_back() {
        // Both answer 204, which has no body at all.
        HttpApiClient client = https(Map.of("/v1/questions/job-1", 204,
                "/v1/questions/job-1/feedback", 204), exchange -> "");

        client.cancel("job-1");
        client.withdrawFeedback("job-1");

        assertEquals(List.of("DELETE /v1/questions/job-1", "DELETE /v1/questions/job-1/feedback"),
                requested);
    }

    @Test
    void a_verdict_carries_the_opinion_and_the_job_id_is_the_whole_address() {
        HttpApiClient client = https(Map.of(), exchange -> Json.write(
                new Models.FeedbackModel("f-1", "job-1", Models.Verdict.NO, "off by one", "pending")));

        Models.FeedbackModel receipt = client.submitFeedback("job-1", Models.Verdict.NO, "off by one");

        assertEquals("pending", receipt.state());
        assertEquals("{\"verdict\":\"no\",\"comment\":\"off by one\"}", bodies.get(0));
    }

    // --- the failures ------------------------------------------------------

    @Test
    void an_error_envelope_becomes_the_exception_the_interface_shows() {
        HttpApiClient client = https(Map.of("/v1/questions/job-1", 409), exchange ->
                "{\"error\": {\"code\": \"job_running\", \"message\": \"still running\"}}");

        ApiException failed = assertThrows(ApiException.class, () -> client.job("job-1"));

        assertEquals("job_running", failed.code());
        assertEquals(409, failed.status());
        assertEquals("still running", failed.getMessage());
        assertTrue(!failed.retryable());
    }

    @Test
    void a_failure_with_no_envelope_still_has_a_code_to_branch_on() {
        HttpApiClient client = https(Map.of("/v1/meta", 502), exchange -> "<html>bad gateway</html>");

        ApiException failed = assertThrows(ApiException.class, client::meta);

        assertEquals("error", failed.code());
        assertTrue(failed.getMessage().contains("502"));
    }

    @Test
    void an_envelope_without_a_code_is_treated_as_having_none() {
        HttpApiClient client = https(Map.of("/v1/meta", 500), exchange -> "{\"error\": {}}");

        assertEquals("error", assertThrows(ApiException.class, client::meta).code());
    }

    @Test
    void readiness_answers_its_own_document_when_it_is_not_ready() {
        // 503 with a readiness document rather than an error envelope,
        // because what is wrong is the interesting part.
        HttpApiClient client = https(Map.of("/readyz", 503), exchange -> Json.write(
                new Models.Readiness(false,
                        Map.of("ollama", new Models.Check(false, "connection refused")), List.of())));

        Models.Readiness readiness = client.readiness();

        assertTrue(!readiness.ready());
        assertEquals("connection refused", readiness.checks().get("ollama").detail());
    }

    @Test
    void a_server_that_is_not_there_is_a_failure_that_names_the_address() {
        Settings settings = Settings.from(Map.of(), "--url", "https://localhost:1", "--insecure");
        HttpApiClient client = new HttpApiClient(settings);

        ApiException failed = assertThrows(ApiException.class, client::meta);

        assertEquals("unreachable", failed.code());
        assertTrue(failed.retryable());
        assertTrue(failed.getMessage().contains("https://localhost:1"));
    }

    @Test
    void a_certificate_this_client_was_not_told_about_is_refused() {
        // The whole point of the development certificate: a client that
        // checks will not accept it until it has been told to.
        HttpApiClient client = https(Map.of(), exchange -> Json.write(Fakes.meta(true)),
                "--fingerprint", "ab".repeat(32));

        ApiException failed = assertThrows(ApiException.class, client::meta);

        assertEquals("unreachable", failed.code());
        // The deepest cause is the one that says something; Java's own
        // message would be "PKIX path building failed".
        assertTrue(failed.getMessage().contains("which is not the pinned"), failed.getMessage());
    }

    @Test
    void insecure_reaches_a_server_nothing_would_otherwise_believe() {
        HttpApiClient client = https(Map.of(), exchange -> Json.write(Fakes.meta(true)),
                "--insecure");

        assertEquals("4.5.0", client.meta().version());
    }

    @Test
    void plain_http_is_a_supported_deployment() {
        // Behind something that terminates TLS itself. `API_TLS_ENABLED=false`
        // is the server side of the same arrangement.
        HttpApiClient client = client(false, Map.of(), exchange -> Json.write(Fakes.meta(false)));

        assertTrue(!client.meta().feedback());
    }

    // --- the event stream --------------------------------------------------

    @Test
    void the_events_url_follows_the_link_the_server_gave_and_carries_no_token() {
        // A browser needs the token in the query string because EventSource
        // cannot set headers. This client can, and a token in a query string
        // is a token in an access log.
        HttpApiClient client = https(Map.of(), exchange -> "", "--token", "s3cret");

        assertTrue(client.eventsUrl(Fakes.queued(), 0).toString()
                .endsWith("/v1/questions/job-1/events"));
        assertTrue(client.eventsUrl(Fakes.queued(), 4).toString().endsWith("events?from_seq=4"));
        assertTrue(!client.eventsUrl(Fakes.queued(), 4).toString().contains("s3cret"));
    }

    @Test
    void a_stream_is_read_line_by_line_and_closed_by_the_caller() throws Exception {
        HttpApiClient client = https(Map.of(), exchange ->
                "event: progress\ndata: {\"seq\":1}\n\nevent: done\ndata: {}\n\n",
                "--token", "s3cret");

        List<String> lines = new ArrayList<>();
        try (ApiClient.Lines stream = client.events(Fakes.queued(), 0)) {
            String line;
            while ((line = stream.readLine()) != null) {
                lines.add(line);
            }
        }

        assertEquals(List.of("event: progress", "data: {\"seq\":1}", "", "event: done",
                "data: {}", ""), lines);
        assertEquals(List.of("Bearer s3cret"), authorisation);
    }

    @Test
    void a_stream_the_server_refused_to_open_is_a_failure_rather_than_an_empty_one() {
        HttpApiClient client = https(Map.of("/v1/questions/job-1/events", 401), exchange -> "no");

        IOException failed = assertThrows(IOException.class,
                () -> client.events(Fakes.queued(), 0));

        assertTrue(failed.getMessage().contains("401"));
    }

    @Test
    void closing_a_stream_that_has_already_failed_does_not_replace_the_failure() throws Exception {
        // Closing is called from the path that is handling the failure, and
        // a throw here would report the tidying up rather than the problem.
        HttpApiClient.ReaderLines lines = new HttpApiClient.ReaderLines(
                new java.io.BufferedReader(new java.io.StringReader("x")) {
                    @Override
                    public void close() throws IOException {
                        throw new IOException("already gone");
                    }
                });

        lines.close();
        assertNotNull(lines);
    }

    @Test
    void an_interrupted_request_is_reported_and_the_interrupt_is_left_standing() throws Exception {
        HttpApiClient client = https(Map.of(), exchange -> {
            // Hold the response until the client has been interrupted.
            try {
                TimeUnit.SECONDS.sleep(5);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
            return "{}";
        });

        CountDownLatch finished = new CountDownLatch(1);
        List<ApiException> failures = new ArrayList<>();
        Thread caller = new Thread(() -> {
            try {
                client.meta();
            } catch (ApiException failure) {
                failures.add(failure);
            } finally {
                finished.countDown();
            }
        });
        caller.start();
        TimeUnit.MILLISECONDS.sleep(200);
        caller.interrupt();

        assertTrue(finished.await(20, TimeUnit.SECONDS));
        assertEquals("interrupted", failures.get(0).code());
    }


    @Test
    void a_service_that_is_still_starting_is_worth_asking_again() {
        HttpApiClient client = https(Map.of("/v1/meta", 503), exchange ->
                "{\"error\": {\"code\": \"unavailable\", \"message\": \"shutting down\"}}");

        ApiException failed = assertThrows(ApiException.class, client::meta);

        assertEquals(503, failed.status());
        assertTrue(failed.retryable());
    }

    @Test
    void a_success_with_no_body_is_an_empty_document_rather_than_a_parse_failure() {
        // 200 and nothing at all, which a proxy in front can produce.
        HttpApiClient client = https(Map.of(), exchange -> "");

        assertEquals("", client.meta().version());
    }

    @Test
    void a_failure_with_no_body_at_all_is_still_a_failure() {
        // The order of the two checks in `send` decides this, and getting it
        // the other way round turns every empty 500 into an empty success.
        HttpApiClient client = https(Map.of("/v1/meta", 500), exchange -> "");

        ApiException failed = assertThrows(ApiException.class, client::meta);
        assertEquals(500, failed.status());
        assertEquals("error", failed.code());
    }

    @Test
    void an_envelope_that_is_json_but_has_no_error_in_it_is_still_a_failure() {
        HttpApiClient client = https(Map.of("/v1/meta", 500), exchange -> "{\"detail\": \"oops\"}");

        assertEquals("error", assertThrows(ApiException.class, client::meta).code());
    }

    @Test
    void a_link_that_already_carries_a_query_gains_the_resume_with_an_ampersand() {
        HttpApiClient client = https(Map.of(), exchange -> "");
        Models.Job job = Fakes.queued();
        Models.Job withQuery = new Models.Job(job.id(), job.status(), job.question(),
                job.metadata(), job.created_at(), job.started_at(), job.finished_at(),
                job.duration_ms(), job.progress(), job.answer(), job.error(),
                new Models.JobLinks(job.links().self(), job.links().events() + "?replay=1"));

        assertTrue(client.eventsUrl(withQuery, 4).toString().endsWith("?replay=1&from_seq=4"));
    }

    @Test
    void a_failure_whose_cause_says_nothing_is_named_by_its_kind() {
        // Java's TLS exceptions nest the useful sentence two or three levels
        // down, and some of them have no sentence at all.
        Settings settings = Settings.from(Map.of(), "--url", "https://no-such-host.invalid:8443");
        HttpApiClient client = new HttpApiClient(settings);

        ApiException failed = assertThrows(ApiException.class, client::meta);

        assertEquals("unreachable", failed.code());
        assertFalse(failed.getMessage().endsWith(": "), failed.getMessage());
    }

    @Test
    void opening_a_stream_on_an_interrupted_thread_reports_it_and_leaves_the_flag() throws Exception {
        HttpApiClient client = https(Map.of(), exchange -> "event: done\ndata: {}\n\n");
        List<String> failures = new ArrayList<>();
        Thread caller = new Thread(() -> {
            Thread.currentThread().interrupt();
            try {
                ApiClient.Lines opened = client.events(Fakes.queued(), 0);
                opened.close();
                failures.add("opened");
            } catch (IOException expected) {
                failures.add(expected.getMessage());
            }
            failures.add("interrupted=" + Thread.currentThread().isInterrupted());
        });
        caller.start();
        caller.join(20_000);

        assertEquals("interrupted while opening the progress stream", failures.get(0));
        assertEquals("interrupted=true", failures.get(1));
    }


    @Test
    void a_cause_with_nothing_to_say_is_named_by_its_kind() {
        // Java's TLS exceptions nest the useful sentence two or three levels
        // down, and some of the levels have no sentence at all.
        assertEquals("connection reset",
                HttpApiClient.reason(new IOException(new IOException("connection reset"))));
        assertEquals("IOException", HttpApiClient.reason(new IOException((String) null)));
        assertEquals("IOException", HttpApiClient.reason(new IOException("   ")));
    }
}
