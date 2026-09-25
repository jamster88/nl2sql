package org.nl2sql.desktop.api;

import com.fasterxml.jackson.databind.JsonNode;
import org.nl2sql.desktop.Settings;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * The HTTP client. Eight calls and one error shape.
 *
 * <p>Deliberately thin: the API is already a good client interface, so
 * wrapping it in a layer with opinions of its own would only add a second
 * place for a bug to live. The one thing worth centralising is the error
 * envelope -- every failure comes back as
 * {@code {"error": {"code", "message"}}}, and code that has to unwrap that at
 * each call site eventually forgets to somewhere.
 *
 * <p>Every call blocks. That is not an oversight: this is a desktop
 * application with a thread to spare, and an asynchronous client would mean
 * every caller handling completion as well as failure. What must never block
 * is the JavaFX thread, and that is enforced where the work is started rather
 * than by making the client harder to read.
 */
public final class HttpApiClient implements ApiClient {

    private final HttpClient http;
    private final Settings settings;

    public HttpApiClient(Settings settings) {
        this(settings, HttpClient.newBuilder()
                .sslContext(Tls.contextFor(settings))
                .sslParameters(Tls.parametersFor(settings))
                .connectTimeout(Duration.ofSeconds(10))
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build());
    }

    /** For the tests, which supply a client pointed at a server on a loopback port. */
    public HttpApiClient(Settings settings, HttpClient http) {
        this.settings = settings;
        this.http = http;
    }

    @Override
    public Models.Meta meta() {
        return Json.read(get("/v1/meta", Map.of()), Models.Meta.class);
    }

    @Override
    public Models.Readiness readiness() {
        return Json.read(get("/readyz", Map.of()), Models.Readiness.class);
    }

    @Override
    public Models.Job ask(String question, int waitSeconds) {
        Map<String, String> query = new LinkedHashMap<>();
        if (waitSeconds > 0) {
            query.put("wait", String.valueOf(waitSeconds));
        }
        String body = Json.write(new Models.AskRequest(question, null, Map.of()));
        return Json.read(send(request("/v1/questions", query)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                .build()), Models.Job.class);
    }

    @Override
    public Models.Job job(String id) {
        return Json.read(get("/v1/questions/" + encode(id), Map.of()), Models.Job.class);
    }

    @Override
    public Models.JobList jobs(int limit) {
        return Json.read(get("/v1/questions", Map.of("limit", String.valueOf(limit))),
                Models.JobList.class);
    }

    @Override
    public void cancel(String id) {
        send(request("/v1/questions/" + encode(id), Map.of()).DELETE().build());
    }

    @Override
    public Models.FeedbackModel submitFeedback(String id, Models.Verdict verdict, String comment) {
        String body = Json.write(new Models.FeedbackRequest(verdict, comment));
        return Json.read(send(request("/v1/questions/" + encode(id) + "/feedback", Map.of())
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                .build()), Models.FeedbackModel.class);
    }

    @Override
    public void withdrawFeedback(String id) {
        send(request("/v1/questions/" + encode(id) + "/feedback", Map.of()).DELETE().build());
    }

    @Override
    public URI eventsUrl(Models.Job job, int fromSeq) {
        Map<String, String> query = new LinkedHashMap<>();
        if (fromSeq > 0) {
            query.put("from_seq", String.valueOf(fromSeq));
        }
        // No `access_token` here, ever. A browser needs it in the query string
        // because EventSource cannot set headers; this client can, and a token
        // in a query string is a token in an access log.
        return url(job.links().events(), query);
    }

    @Override
    public Lines events(Models.Job job, int fromSeq) throws IOException {
        HttpRequest request = HttpRequest.newBuilder(eventsUrl(job, fromSeq))
                .header("Accept", "text/event-stream")
                // No timeout. This request is meant to stay open for as long
                // as the question takes, and a request timeout here would
                // close a healthy stream mid-answer.
                .headers(authorisation())
                .GET()
                .build();
        HttpResponse<InputStream> response;
        try {
            response = http.send(request, HttpResponse.BodyHandlers.ofInputStream());
        } catch (InterruptedException cause) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted while opening the progress stream", cause);
        }
        if (response.statusCode() != 200) {
            response.body().close();
            throw new IOException("the progress stream answered " + response.statusCode());
        }
        return new ReaderLines(new BufferedReader(
                new InputStreamReader(response.body(), StandardCharsets.UTF_8)));
    }

    // --- the plumbing ------------------------------------------------------

    private String get(String path, Map<String, String> query) {
        return send(request(path, query).GET().build());
    }

    private HttpRequest.Builder request(String path, Map<String, String> query) {
        return HttpRequest.newBuilder(url(path, query))
                .header("Accept", "application/json")
                .timeout(Duration.ofSeconds(settings.waitSeconds() + 30L))
                .headers(authorisation());
    }

    /**
     * The token, as a header, or nothing at all.
     *
     * <p>{@code headers()} takes pairs and rejects an odd number, so "no
     * token" has to be an empty array rather than an empty value -- an
     * {@code Authorization: Bearer} with nothing after it is a 401 that looks
     * like a server problem.
     */
    private String[] authorisation() {
        return settings.authenticated()
                ? new String[] {"Authorization", "Bearer " + settings.token()}
                : new String[0];
    }

    private URI url(String path, Map<String, String> query) {
        StringBuilder built = new StringBuilder(settings.baseUrl().toString()).append(path);
        String separator = path.contains("?") ? "&" : "?";
        for (Map.Entry<String, String> entry : query.entrySet()) {
            built.append(separator).append(entry.getKey()).append('=').append(encode(entry.getValue()));
            separator = "&";
        }
        return URI.create(built.toString());
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private String send(HttpRequest request) {
        HttpResponse<String> response;
        try {
            response = http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        } catch (IOException cause) {
            // A network-level failure has no envelope to read, so it is given
            // the same shape as one: the interface shows every failure alike.
            throw new ApiException(0, "unreachable",
                    "cannot reach the API at " + settings.baseUrl() + ": " + reason(cause));
        } catch (InterruptedException cause) {
            Thread.currentThread().interrupt();
            throw new ApiException(0, "interrupted", "the request was interrupted");
        }

        int status = response.statusCode();
        String body = response.body();
        // No lower bound: `HttpResponse.statusCode()` is never below 200 for
        // a response the client handed back -- an informational status is
        // consumed by the protocol layer -- so testing for one would be a
        // branch no server can take.
        if (status < 300) {
            // 204 has no body by definition, and a proxy in front can turn
            // any success into an empty one. An empty document parses into a
            // model whose fields are all their defaults, which is what those
            // defaults are for.
            return body.isEmpty() ? "{}" : body;
        }
        // `/readyz` answers 503 with a readiness document rather than an error
        // envelope, because what is wrong is the interesting part.
        if (status == 503 && request.uri().getPath().endsWith("/readyz")) {
            return body;
        }
        throw described(status, body);
    }

    private static ApiException described(int status, String body) {
        JsonNode error;
        try {
            error = Json.tree(body).path("error");
        } catch (ApiException cause) {
            return new ApiException(status, "error", "HTTP " + status + ": " + body);
        }
        if (error.isMissingNode() || !error.hasNonNull("code")) {
            return new ApiException(status, "error", "HTTP " + status);
        }
        return new ApiException(status, error.get("code").asText(),
                error.path("message").asText("HTTP " + status));
    }

    /**
     * Why a connection failed, said once.
     *
     * <p>Java's TLS exceptions nest the useful sentence two or three levels
     * down and put a class name at the top, so the message a user would see
     * is {@code javax.net.ssl.SSLHandshakeException: PKIX path building
     * failed} -- which names neither the certificate nor what to do. The
     * deepest cause is the one that says something.
     */
    static String reason(Throwable cause) {
        Throwable deepest = cause;
        while (deepest.getCause() != null) {
            deepest = deepest.getCause();
        }
        String message = deepest.getMessage();
        return message == null || message.isBlank() ? deepest.getClass().getSimpleName() : message;
    }

    /** A {@link BufferedReader} seen as {@link Lines}. */
    record ReaderLines(BufferedReader reader) implements Lines {

        @Override
        public String readLine() throws IOException {
            return reader.readLine();
        }

        @Override
        public void close() {
            try {
                reader.close();
            } catch (IOException ignored) {
                // Closing a stream that has already failed is not news, and
                // this is called from the path that is handling the failure.
                // Letting it throw would replace a message about what went
                // wrong with one about the tidying up afterwards.
            }
        }
    }
}
