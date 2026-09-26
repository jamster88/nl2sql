package org.nl2sql.desktop.api;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.nl2sql.desktop.Settings;

import javax.net.ssl.X509TrustManager;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.cert.CertificateException;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TlsTest {

    private static Settings with(String... arguments) {
        return Settings.from(Map.of(), arguments);
    }

    private static X509Certificate certificate() throws Exception {
        try (var stream = Files.newInputStream(TestCertificate.pem())) {
            return (X509Certificate) CertificateFactory.getInstance("X.509")
                    .generateCertificate(stream);
        }
    }

    @Test
    void every_way_of_checking_produces_a_context() {
        assertNotNull(Tls.contextFor(with()));
        assertNotNull(Tls.contextFor(with("--insecure")));
        assertNotNull(Tls.contextFor(with("--fingerprint", "ab".repeat(32))));
        assertNotNull(Tls.contextFor(with("--cacert", TestCertificate.pem().toString())));
        assertNotNull(Tls.contextFor(with("--url", "http://localhost:8443")));
    }

    @Test
    void only_insecure_turns_off_the_check_that_the_name_matches() {
        // Leaving it on with a trust manager that accepts anything fails in
        // an exciting way -- accepted, then dropped over the hostname -- and
        // looks nothing like what it is.
        assertEquals("HTTPS", Tls.parametersFor(with()).getEndpointIdentificationAlgorithm());
        assertEquals(null, Tls.parametersFor(with("--insecure")).getEndpointIdentificationAlgorithm());
    }

    @Test
    void the_status_bar_is_told_which_of_the_four_it_is() {
        assertEquals("verified", Tls.describe(with()));
        assertEquals("NOT VERIFIED", Tls.describe(with("--insecure")));
        assertEquals("not encrypted", Tls.describe(with("--url", "http://localhost:8443")));
        assertTrue(Tls.describe(with("--fingerprint", "ab".repeat(32))).startsWith("pinned to abab"));
        assertTrue(Tls.describe(with("--cacert", "/tmp/nl2sql-api.crt"))
                .equals("verified against nl2sql-api.crt"));
    }

    @Test
    void a_fingerprint_is_the_sha256_the_server_prints() throws Exception {
        String fingerprint = Tls.fingerprintOf(certificate());

        assertTrue(fingerprint.matches("[0-9a-f]{64}"), fingerprint);
    }

    @Test
    void a_pin_accepts_the_certificate_it_names() throws Exception {
        X509Certificate presented = certificate();
        X509TrustManager pinned = new Tls.TrustOneCertificate(Tls.fingerprintOf(presented));

        pinned.checkServerTrusted(new X509Certificate[] {presented}, "RSA");
        pinned.checkClientTrusted(new X509Certificate[] {presented}, "RSA");
        assertEquals(0, pinned.getAcceptedIssuers().length);
    }

    @Test
    void a_pin_refuses_a_different_certificate_and_says_why() throws Exception {
        X509TrustManager pinned = new Tls.TrustOneCertificate("ab".repeat(32));

        CertificateException refused = assertThrows(CertificateException.class,
                () -> pinned.checkServerTrusted(new X509Certificate[] {certificate()}, "RSA"));
        // The likeliest cause, named: the API writes a new certificate when
        // the names it must cover change, and says so at start-up.
        assertTrue(refused.getMessage().contains("writes a new certificate"));
    }

    @Test
    void a_pin_refuses_a_server_that_presented_nothing() {
        X509TrustManager pinned = new Tls.TrustOneCertificate("ab".repeat(32));

        assertThrows(CertificateException.class,
                () -> pinned.checkServerTrusted(new X509Certificate[0], "RSA"));
        assertThrows(CertificateException.class, () -> pinned.checkServerTrusted(null, "RSA"));
    }

    @Test
    void trusting_everything_trusts_everything() throws Exception {
        X509TrustManager anything = new Tls.TrustEverything();

        anything.checkServerTrusted(new X509Certificate[] {certificate()}, "RSA");
        anything.checkClientTrusted(null, "RSA");
        assertEquals(0, anything.getAcceptedIssuers().length);
    }

    @Test
    void a_certificate_file_becomes_a_trust_manager() {
        assertNotNull(Tls.trustManagerFor(TestCertificate.pem()).getAcceptedIssuers());
    }

    @Test
    void a_certificate_file_that_is_not_there_says_where_it_comes_from() {
        // Under compose it comes out of the apitls volume, and that is the
        // sentence somebody needs rather than "NoSuchFileException".
        ApiException failed = assertThrows(ApiException.class,
                () -> Tls.trustManagerFor(Path.of("/nowhere/nl2sql-api.crt")));

        assertEquals("tls_unusable", failed.code());
        assertTrue(failed.getMessage().contains("docker compose"));
    }

    @Test
    void a_file_that_is_not_a_certificate_is_refused(@TempDir Path scratch) throws Exception {
        Path notACertificate = scratch.resolve("notes.txt");
        Files.writeString(notACertificate, "this is not a certificate\n");

        ApiException failed = assertThrows(ApiException.class,
                () -> Tls.trustManagerFor(notACertificate));
        assertEquals("tls_unusable", failed.code());
    }

    @Test
    void an_empty_file_is_refused_as_having_no_certificate_in_it(@TempDir Path scratch)
            throws Exception {
        Path empty = scratch.resolve("empty.crt");
        Files.writeString(empty, "");

        assertTrue(assertThrows(ApiException.class, () -> Tls.trustManagerFor(empty))
                .getMessage().contains("no certificate in"));
    }


    @Test
    void a_jvm_without_the_protocol_says_so_rather_than_failing_later() {
        ApiException failed = assertThrows(ApiException.class,
                () -> Tls.context(null, "no-such-protocol"));

        assertEquals("tls_unusable", failed.code());
        assertTrue(failed.getMessage().contains("cannot configure TLS"));
    }

    @Test
    void a_factory_that_produced_no_x509_manager_is_a_named_failure() {
        assertNotNull(Tls.x509(new javax.net.ssl.TrustManager[] {new Tls.TrustEverything()}));

        assertTrue(assertThrows(ApiException.class,
                () -> Tls.x509(new javax.net.ssl.TrustManager[0]))
                .getMessage().contains("no X.509 manager"));
        assertTrue(assertThrows(ApiException.class,
                () -> Tls.x509(new javax.net.ssl.TrustManager[] {new javax.net.ssl.TrustManager() {
                }}))
                .getMessage().contains("no X.509 manager"));
    }

    @Test
    void a_key_store_kind_this_jvm_does_not_have_is_reported_against_the_file() {
        ApiException failed = assertThrows(ApiException.class,
                () -> Tls.trustManagerFor(TestCertificate.pem(), "no-such-store"));

        assertEquals("tls_unusable", failed.code());
        assertTrue(failed.getMessage().contains("cannot build a trust store from"));
    }

    @Test
    void a_jvm_without_the_digest_cannot_fingerprint_anything() throws Exception {
        assertThrows(IllegalStateException.class,
                () -> Tls.fingerprintOf(certificate(), "no-such-digest"));
    }
}
