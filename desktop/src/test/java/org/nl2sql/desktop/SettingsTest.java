package org.nl2sql.desktop;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

import java.nio.file.Path;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SettingsTest {

    private static final Map<String, String> NOTHING = Map.of();

    @Test
    void the_defaults_are_the_compose_deployment() {
        Settings settings = Settings.from(NOTHING);

        assertEquals(Settings.DEFAULT_BASE_URL, settings.baseUrl().toString());
        assertEquals("", settings.token());
        assertNull(settings.caCert());
        assertEquals("", settings.fingerprint());
        assertFalse(settings.insecure());
        assertFalse(settings.authenticated());
        assertEquals(Settings.DEFAULT_WAIT_SECONDS, settings.waitSeconds());
        assertEquals(Settings.DEFAULT_POLL_INTERVAL_MS, settings.pollIntervalMs());
        assertEquals(Settings.DEFAULT_HISTORY_LIMIT, settings.historyLimit());
    }

    @Test
    void the_environment_is_read() {
        Settings settings = Settings.from(Map.of(
                "NL2SQL_API_URL", "https://nl2sql.example.com:9443",
                "NL2SQL_API_TOKEN", "s3cret",
                "NL2SQL_API_CACERT", "/tmp/api.crt",
                "NL2SQL_API_WAIT_SECONDS", "30",
                "NL2SQL_POLL_INTERVAL_MS", "500",
                "NL2SQL_HISTORY_LIMIT", "5"));

        assertEquals("https://nl2sql.example.com:9443", settings.baseUrl().toString());
        assertEquals("s3cret", settings.token());
        assertTrue(settings.authenticated());
        assertEquals(Path.of("/tmp/api.crt"), settings.caCert());
        assertEquals(30, settings.waitSeconds());
        assertEquals(500, settings.pollIntervalMs());
        assertEquals(5, settings.historyLimit());
    }

    @Test
    void a_flag_beats_the_environment() {
        Settings settings = Settings.from(
                Map.of("NL2SQL_API_URL", "https://from-the-environment:8443",
                        "NL2SQL_API_TOKEN", "old"),
                "--url", "https://from-the-flag:8443", "--token", "new", "--wait", "12");

        assertEquals("https://from-the-flag:8443", settings.baseUrl().toString());
        assertEquals("new", settings.token());
        assertEquals(12, settings.waitSeconds());
    }

    @Test
    void an_empty_variable_is_an_absent_one() {
        // Compose passes an unset variable through as an empty string, and
        // this client is started from a compose-shaped environment often
        // enough for the difference to matter.
        Settings settings = Settings.from(Map.of("NL2SQL_API_URL", "  ", "NL2SQL_API_TOKEN", ""));

        assertEquals(Settings.DEFAULT_BASE_URL, settings.baseUrl().toString());
        assertFalse(settings.authenticated());
    }

    @Test
    void a_trailing_slash_is_dropped_so_paths_do_not_double_up() {
        assertEquals("https://localhost:8443",
                Settings.from(Map.of("NL2SQL_API_URL", "https://localhost:8443/")).baseUrl().toString());
    }

    @ParameterizedTest
    @ValueSource(strings = {"true", "TRUE", "1", "yes"})
    void insecure_is_set_by_any_of_the_words_people_use(String said) {
        assertTrue(Settings.from(Map.of("NL2SQL_API_INSECURE", said)).insecure());
    }

    @ParameterizedTest
    @ValueSource(strings = {"false", "0", "no", "off", "maybe"})
    void anything_else_leaves_verification_on(String said) {
        assertFalse(Settings.from(Map.of("NL2SQL_API_INSECURE", said)).insecure());
    }

    @Test
    void the_insecure_flag_is_set_by_the_flag_too() {
        assertTrue(Settings.from(NOTHING, "--insecure").insecure());
    }

    @Test
    void insecure_and_a_certificate_together_are_refused() {
        // One of them says to check the certificate and the other says not to
        // bother, and guessing which was meant is how a client ends up not
        // checking on the run where it mattered.
        IllegalArgumentException refused = assertThrows(IllegalArgumentException.class,
                () -> Settings.from(NOTHING, "--insecure", "--cacert", "/tmp/api.crt"));
        assertTrue(refused.getMessage().contains("cannot be combined"));
    }

    @Test
    void insecure_and_a_fingerprint_together_are_refused() {
        assertThrows(IllegalArgumentException.class,
                () -> Settings.from(NOTHING, "--insecure", "--fingerprint", "ab".repeat(32)));
    }

    @ParameterizedTest
    @CsvSource({
            "AB:CD, false",
            "'', true",
    })
    void a_fingerprint_is_sixty_four_hexadecimal_characters(String given, boolean accepted) {
        if (accepted) {
            assertEquals("", Settings.from(NOTHING, "--fingerprint", given).fingerprint());
        } else {
            assertThrows(IllegalArgumentException.class,
                    () -> Settings.from(NOTHING, "--fingerprint", given));
        }
    }

    @Test
    void a_fingerprint_is_accepted_however_it_was_pasted() {
        String plain = "3f".repeat(32);
        String grouped = String.join(":", plain.split("(?<=\\G..)"));

        assertEquals(plain, Settings.from(NOTHING, "--fingerprint", grouped.toUpperCase()).fingerprint());
        assertEquals(plain, Settings.from(NOTHING, "--fingerprint", plain).fingerprint());
    }

    @Test
    void an_unknown_flag_is_refused_with_the_usage() {
        IllegalArgumentException refused = assertThrows(IllegalArgumentException.class,
                () -> Settings.from(NOTHING, "--verbose"));
        assertTrue(refused.getMessage().startsWith("unknown option: --verbose"));
        assertTrue(refused.getMessage().contains("--insecure"));
    }

    @Test
    void a_flag_with_nothing_after_it_is_refused() {
        assertTrue(assertThrows(IllegalArgumentException.class,
                () -> Settings.from(NOTHING, "--token")).getMessage().contains("needs a value"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"nonsense", "0", "-3"})
    void a_wait_that_is_not_a_positive_number_is_refused(String given) {
        assertThrows(IllegalArgumentException.class, () -> Settings.from(NOTHING, "--wait", given));
    }

    @Test
    void a_bad_number_in_the_environment_names_the_variable() {
        assertTrue(assertThrows(IllegalArgumentException.class,
                () -> Settings.from(Map.of("NL2SQL_HISTORY_LIMIT", "lots")))
                .getMessage().contains("NL2SQL_HISTORY_LIMIT"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"localhost:8443", "not a url at all", "https://"})
    void an_address_without_a_scheme_and_a_host_is_refused(String given) {
        assertThrows(IllegalArgumentException.class,
                () -> Settings.from(Map.of("NL2SQL_API_URL", given)));
    }

    @Test
    void verdicts_are_kept_under_the_home_directory_by_default() {
        Settings settings = Settings.from(Map.of("HOME", "/home/someone"));

        assertEquals(Path.of("/home/someone/.nl2sql/feedback.json"), settings.feedbackFile());
    }

    @Test
    void no_home_directory_means_verdicts_last_the_session_only() {
        // Ordinary on a locked-down machine and in a container, and a smaller
        // loss than refusing to start.
        assertNull(Settings.from(NOTHING).feedbackFile());
    }

    @Test
    void the_file_can_be_named() {
        assertEquals(Path.of("/tmp/verdicts.json"),
                Settings.from(Map.of("NL2SQL_FEEDBACK_FILE", "/tmp/verdicts.json")).feedbackFile());
    }

    @Test
    void the_file_is_turned_off_by_a_word_rather_than_by_emptiness() {
        // Empty already means "unset" here, because that is how compose
        // passes a variable nobody set -- which is nearly every run.
        assertNull(Settings.from(
                Map.of("HOME", "/home/someone", "NL2SQL_FEEDBACK_FILE", Settings.NO_FILE))
                .feedbackFile());
        assertEquals(Path.of("/home/someone/.nl2sql/feedback.json"),
                Settings.from(Map.of("HOME", "/home/someone", "NL2SQL_FEEDBACK_FILE", " "))
                        .feedbackFile());
    }

    @ParameterizedTest
    @ValueSource(strings = {"--help", "-h"})
    void help_is_asked_for_by_either_spelling(String flag) {
        assertTrue(Settings.wantsHelp(flag));
        assertTrue(Settings.wantsHelp("--url", "https://x", flag));
    }

    @Test
    void help_is_not_asked_for_by_anything_else() {
        assertFalse(Settings.wantsHelp("--insecure"));
        assertFalse(Settings.wantsHelp());
    }

    @Test
    void the_usage_names_every_flag_the_parser_takes() {
        String usage = Settings.usage();
        for (String flag : new String[] {"--url", "--token", "--cacert", "--fingerprint",
                "--insecure", "--wait", "--help"}) {
            assertTrue(usage.contains(flag), usage + " does not mention " + flag);
        }
        assertTrue(usage.contains("NL2SQL_API_URL"));
        assertTrue(usage.contains("NL2SQL_FEEDBACK_FILE"));
    }


    @Test
    void an_address_with_a_host_but_no_scheme_is_refused_too() {
        // `//nl2sql/v1` parses, and has a host and no scheme -- which is the
        // other half of the check, and the half a valid URL never exercises.
        assertThrows(IllegalArgumentException.class,
                () -> Settings.from(Map.of("NL2SQL_API_URL", "//nl2sql-api:8443")));
    }
}
