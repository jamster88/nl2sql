package org.nl2sql.desktop;

import java.net.URI;
import java.net.URISyntaxException;
import java.nio.file.Path;
import java.util.Map;

/**
 * Where the API is, and how this client is allowed to believe it.
 *
 * <p>A browser gets all of this for nothing: {@code gui/} is served by the
 * same nginx that proxies the API, so the page talks to its own origin, the
 * proxy holds the token and the operating system already decided which
 * certificates to trust. A desktop application has none of that. It opens the
 * connection itself, from a machine that has never heard of this server, and
 * every one of those three has to be said out loud.
 *
 * <p>So the settings are the three answers the API's own documentation gives
 * for a self-signed certificate, in the order it recommends them: trust the
 * certificate file, pin its fingerprint, or -- for a throwaway experiment --
 * turn verification off. There is no fourth option in which it silently does
 * not matter.
 *
 * <p>Every setting is an environment variable, and every environment variable
 * has a flag that overrides it, so a shell that is set up for one server can
 * still be pointed at another for one run.
 */
public record Settings(
        URI baseUrl,
        String token,
        Path caCert,
        String fingerprint,
        boolean insecure,
        int waitSeconds,
        int pollIntervalMs,
        int historyLimit,
        Path feedbackFile) {

    /** The compose deployment, published on the host. */
    public static final String DEFAULT_BASE_URL = "https://localhost:8443";

    /**
     * How long to let the server hold the POST open.
     *
     * <p>Zero would be just as correct -- the stream reports progress either
     * way -- but a client that also waits gets the answer in one round trip
     * when the pipeline happens to be quick, and falls back to the stream
     * when it is not.
     */
    public static final int DEFAULT_WAIT_SECONDS = 240;

    public static final int DEFAULT_POLL_INTERVAL_MS = 2000;

    /** How many answered questions stay in the session list. Matches the web GUI. */
    public static final int DEFAULT_HISTORY_LIMIT = 20;

    /**
     * What to set {@code NL2SQL_FEEDBACK_FILE} to for no file at all.
     *
     * <p>A word rather than an empty string, because empty already means
     * something else here: compose passes an unset variable through as one,
     * so "empty turns it off" would turn it off for everybody running under
     * compose, which is nearly everybody.
     */
    public static final String NO_FILE = "none";

    public Settings {
        if (insecure && (caCert != null || !fingerprint.isEmpty())) {
            throw new IllegalArgumentException(
                    "--insecure cannot be combined with --cacert or --fingerprint: one of them "
                            + "says to check the certificate and the other says not to bother");
        }
    }

    /**
     * Read the environment, then let the arguments override it.
     *
     * <p>Both are passed in rather than read from {@code System}, so the whole
     * of this can be tested without a process to set variables in.
     */
    public static Settings from(Map<String, String> environment, String... arguments) {
        String baseUrl = value(environment, "NL2SQL_API_URL", DEFAULT_BASE_URL);
        String token = value(environment, "NL2SQL_API_TOKEN", "");
        String caCert = value(environment, "NL2SQL_API_CACERT", "");
        String fingerprint = value(environment, "NL2SQL_API_FINGERPRINT", "");
        boolean insecure = flag(value(environment, "NL2SQL_API_INSECURE", "false"));
        int waitSeconds = number(environment, "NL2SQL_API_WAIT_SECONDS", DEFAULT_WAIT_SECONDS);
        int pollIntervalMs = number(environment, "NL2SQL_POLL_INTERVAL_MS", DEFAULT_POLL_INTERVAL_MS);
        int historyLimit = number(environment, "NL2SQL_HISTORY_LIMIT", DEFAULT_HISTORY_LIMIT);
        String feedbackFile = value(environment, "NL2SQL_FEEDBACK_FILE", defaultFeedbackFile(environment));

        for (int index = 0; index < arguments.length; index++) {
            String argument = arguments[index];
            switch (argument) {
                case "--url" -> baseUrl = next(arguments, ++index, argument);
                case "--token" -> token = next(arguments, ++index, argument);
                case "--cacert" -> caCert = next(arguments, ++index, argument);
                case "--fingerprint" -> fingerprint = next(arguments, ++index, argument);
                case "--insecure" -> insecure = true;
                case "--wait" -> waitSeconds = positive(next(arguments, ++index, argument), argument);
                default -> throw new IllegalArgumentException(
                        "unknown option: " + argument + "\n\n" + usage());
            }
        }

        return new Settings(
                uri(baseUrl),
                token,
                caCert.isEmpty() ? null : Path.of(caCert),
                normaliseFingerprint(fingerprint),
                insecure,
                waitSeconds,
                pollIntervalMs,
                historyLimit,
                feedbackFile.isEmpty() || feedbackFile.equals(NO_FILE)
                        ? null
                        : Path.of(feedbackFile));
    }

    /** True when a bearer token should be presented. */
    public boolean authenticated() {
        return !token.isEmpty();
    }

    public static String usage() {
        return """
                Usage: java -jar nl2sql-desktop.jar [options]

                  --url URL            the API to talk to (default %s)
                  --token TOKEN        bearer token, when the server requires one
                  --cacert FILE        PEM certificate to verify the server against
                  --fingerprint HEX    accept exactly the certificate with this SHA-256
                  --insecure           do not verify the certificate at all
                  --wait SECONDS       how long to let the server hold a question open
                  --help               show this message

                Verdicts you give are also kept in ~/.nl2sql/feedback.json, so one
                that could not be sent is still on screen after a restart. Set
                NL2SQL_FEEDBACK_FILE to another path, or to "none" for no file.

                Each option has an environment variable: NL2SQL_API_URL,
                NL2SQL_API_TOKEN, NL2SQL_API_CACERT, NL2SQL_API_FINGERPRINT,
                NL2SQL_API_INSECURE and NL2SQL_API_WAIT_SECONDS. The flag wins.

                The API writes itself a self-signed certificate on first start, so
                one of --cacert, --fingerprint or --insecure is needed to reach it:

                  docker compose --profile api cp api:/etc/nl2sql/tls/server.crt ./nl2sql-api.crt
                  java -jar nl2sql-desktop.jar --cacert ./nl2sql-api.crt

                ./start.sh --desktop does that copy for you.
                """.formatted(DEFAULT_BASE_URL);
    }

    /** True when the arguments are asking for the usage message rather than a run. */
    public static boolean wantsHelp(String... arguments) {
        for (String argument : arguments) {
            if (argument.equals("--help") || argument.equals("-h")) {
                return true;
            }
        }
        return false;
    }

    private static String defaultFeedbackFile(Map<String, String> environment) {
        String home = value(environment, "HOME", "");
        // No home directory is an ordinary thing on a locked-down machine and
        // in a container. The verdicts then live for the session only, which
        // is a smaller loss than refusing to start.
        return home.isEmpty() ? "" : Path.of(home, ".nl2sql", "feedback.json").toString();
    }

    private static String value(Map<String, String> environment, String key, String fallback) {
        String found = environment.get(key);
        // Empty is absent: compose passes an unset variable through as an
        // empty string, and this client is started from a compose-shaped
        // environment often enough for the difference to matter.
        return found == null || found.isBlank() ? fallback : found.trim();
    }

    private static boolean flag(String value) {
        return value.equalsIgnoreCase("true") || value.equals("1") || value.equalsIgnoreCase("yes");
    }

    private static int number(Map<String, String> environment, String key, int fallback) {
        String found = value(environment, key, "");
        return found.isEmpty() ? fallback : positive(found, key);
    }

    private static int positive(String value, String what) {
        int parsed;
        try {
            parsed = Integer.parseInt(value);
        } catch (NumberFormatException cause) {
            throw new IllegalArgumentException(what + " must be a whole number, not " + value);
        }
        if (parsed <= 0) {
            throw new IllegalArgumentException(what + " must be greater than zero, not " + value);
        }
        return parsed;
    }

    private static String next(String[] arguments, int index, String flag) {
        if (index >= arguments.length) {
            throw new IllegalArgumentException(flag + " needs a value");
        }
        return arguments[index];
    }

    private static URI uri(String value) {
        URI parsed;
        try {
            parsed = new URI(value.endsWith("/") ? value.substring(0, value.length() - 1) : value);
        } catch (URISyntaxException cause) {
            throw new IllegalArgumentException("not a usable API address: " + value);
        }
        if (parsed.getScheme() == null || parsed.getHost() == null) {
            throw new IllegalArgumentException(
                    "the API address needs a scheme and a host, like " + DEFAULT_BASE_URL
                            + " -- got " + value);
        }
        return parsed;
    }

    /**
     * A fingerprint as the server prints it, however it was pasted.
     *
     * <p>The API prints plain lowercase hex; every other tool that shows one
     * groups it in colon-separated pairs, and a user who pins the one their
     * browser showed them should not have to know which this expects.
     */
    private static String normaliseFingerprint(String value) {
        String stripped = value.replace(":", "").replace(" ", "").toLowerCase(java.util.Locale.ROOT);
        if (stripped.isEmpty()) {
            return "";
        }
        if (!stripped.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(
                    "a SHA-256 fingerprint is 64 hexadecimal characters; got " + value);
        }
        return stripped;
    }
}
