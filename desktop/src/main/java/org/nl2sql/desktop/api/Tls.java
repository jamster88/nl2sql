package org.nl2sql.desktop.api;

import org.nl2sql.desktop.Settings;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLParameters;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;
import java.io.IOException;
import java.io.InputStream;
import java.net.http.HttpClient;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.Certificate;
import java.security.cert.CertificateEncodingException;
import java.security.cert.CertificateException;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.time.Duration;
import java.util.HexFormat;
import java.util.List;

/**
 * Deciding whether to believe the server.
 *
 * <p>The stack issues the API and the sign-in service certificates of their
 * own from a development CA it makes itself, which every client that checks
 * will refuse -- and that refusal is the feature, not the obstacle. What a
 * client owes its user is a way to say <em>which</em> server it meant, and
 * three of them exist here, in the order the API's own documentation
 * recommends:
 *
 * <ol>
 *   <li><b>A certificate file.</b> The strongest, and no harder than the
 *       others: one {@code docker compose cp} of the CA's certificate and
 *       both connections are verified against it, by name.
 *   <li><b>A fingerprint.</b> For when copying a file around is awkward. Each
 *       server prints its own at start-up, the API's is in {@code /v1/meta},
 *       and both are given, comma-separated.
 *   <li><b>Nothing at all.</b> For a throwaway experiment, and it says so in
 *       the status bar for as long as it is on, because an application that
 *       stops verifying quietly is how it ends up doing it in production.
 * </ol>
 *
 * <p>The second and third build a trust manager by hand, which is normally a
 * mistake worth failing a review over. It is not one here: neither of them is
 * <em>weakening</em> a chain of trust, because there is no chain -- a
 * self-signed certificate has no issuer to check against, so the only question
 * that can be asked is whether this is the one the user named.
 */
public final class Tls {

    /** Every JVM has it, and it is what the API's own server offers. */
    private static final String PROTOCOL = "TLS";

    private Tls() {
    }

    /** The context an {@link java.net.http.HttpClient} should be built with. */
    public static SSLContext contextFor(Settings settings) {
        if (!settings.baseUrl().getScheme().equals("https")) {
            // Plain HTTP, which is a supported deployment: something in front
            // already terminates TLS. Nothing here applies, and building a
            // context anyway would only hide that from a reader.
            return context(null, PROTOCOL);
        }
        if (settings.insecure()) {
            return context(new TrustManager[] {new TrustEverything()}, PROTOCOL);
        }
        if (!settings.fingerprint().isEmpty()) {
            return context(new TrustManager[] {new TrustPinned(settings.fingerprint())}, PROTOCOL);
        }
        if (settings.caCert() != null) {
            return context(new TrustManager[] {trustManagerFor(settings.caCert())}, PROTOCOL);
        }
        return context(null, PROTOCOL);
    }

    /**
     * An HTTP client that checks certificates as the settings say.
     *
     * <p>One for the API and one for the sign-in service, built the same way,
     * because the two present the same certificate and a difference between
     * them would be a bug in whichever was written second.
     */
    public static HttpClient client(Settings settings) {
        return HttpClient.newBuilder()
                .sslContext(contextFor(settings))
                .sslParameters(parametersFor(settings))
                .connectTimeout(Duration.ofSeconds(10))
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
    }

    /**
     * The parameters that go with it.
     *
     * <p>Only {@code --insecure} changes them, and what it changes is the
     * check that the certificate is for the host that presented it. Leaving
     * that on with a trust manager that accepts anything is a combination that
     * fails in an exciting way -- the certificate is accepted, then the
     * connection is dropped over the name -- and looks nothing like what it is.
     */
    public static SSLParameters parametersFor(Settings settings) {
        SSLParameters parameters = new SSLParameters();
        parameters.setEndpointIdentificationAlgorithm(settings.insecure() ? null : "HTTPS");
        return parameters;
    }

    /** One line for the status bar: how this connection is being checked. */
    public static String describe(Settings settings) {
        if (!settings.baseUrl().getScheme().equals("https")) {
            return "not encrypted";
        }
        if (settings.insecure()) {
            return "NOT VERIFIED";
        }
        if (!settings.fingerprint().isEmpty()) {
            int pins = settings.fingerprint().split(",").length;
            return "pinned to " + settings.fingerprint().substring(0, 12)
                    + (pins > 1 ? " and " + (pins - 1) + " more" : "");
        }
        if (settings.caCert() != null) {
            return "verified against " + settings.caCert().getFileName();
        }
        return "verified";
    }

    /** The SHA-256 of a certificate, in the form the API prints it. */
    public static String fingerprintOf(X509Certificate certificate) {
        return fingerprintOf(certificate, "SHA-256");
    }

    /**
     * The same, with the digest named.
     *
     * <p>Named only so the failure below can be reached from a test: SHA-256
     * is required of every JVM and a certificate that got this far has
     * already been decoded once, so the catch is unreachable in production
     * and is still what decides whether a broken JVM produces a sentence.
     */
    static String fingerprintOf(X509Certificate certificate, String digest) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance(digest).digest(certificate.getEncoded()));
        } catch (GeneralSecurityException cause) {
            throw new IllegalStateException("cannot fingerprint a certificate", cause);
        }
    }

    /** Every certificate in a PEM file, as a trust manager that accepts chains they signed. */
    static X509TrustManager trustManagerFor(Path pem) {
        return trustManagerFor(pem, KeyStore.getDefaultType());
    }

    /**
     * The same, for a named kind of key store.
     *
     * <p>The kind is a parameter only so the failure below can be reached
     * from a test. Everything in this method is documented as always
     * available, which means the branch that reports it missing is one that
     * cannot be taken and therefore one nobody has ever seen work -- and it
     * is the branch that decides whether a bad certificate file produces a
     * sentence or a stack trace.
     */
    static X509TrustManager trustManagerFor(Path pem, String storeType) {
        List<? extends Certificate> certificates = readPem(pem);
        try {
            KeyStore store = KeyStore.getInstance(storeType);
            store.load(null, null);
            int index = 0;
            for (Certificate certificate : certificates) {
                store.setCertificateEntry("nl2sql-" + index++, certificate);
            }
            TrustManagerFactory factory =
                    TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
            factory.init(store);
            return x509(factory.getTrustManagers());
        } catch (GeneralSecurityException | IOException cause) {
            throw new ApiException(0, "tls_unusable",
                    "cannot build a trust store from " + pem + ": " + cause.getMessage());
        }
    }

    /** The X.509 manager among them, which every JVM's factory produces. */
    static X509TrustManager x509(TrustManager[] managers) {
        for (TrustManager manager : managers) {
            if (manager instanceof X509TrustManager found) {
                return found;
            }
        }
        throw new ApiException(0, "tls_unusable",
                "this JVM's trust manager factory produced no X.509 manager");
    }

    private static List<? extends Certificate> readPem(Path pem) {
        try (InputStream stream = Files.newInputStream(pem)) {
            List<? extends Certificate> found =
                    List.copyOf(CertificateFactory.getInstance("X.509").generateCertificates(stream));
            if (found.isEmpty()) {
                throw new ApiException(0, "tls_unusable",
                        "there is no certificate in " + pem);
            }
            return found;
        } catch (IOException cause) {
            throw new ApiException(0, "tls_unusable",
                    "cannot read the certificate at " + pem + ": " + cause.getMessage()
                            + " -- under compose it is copied out with: docker compose "
                            + "--profile api cp api:/etc/nl2sql/tls/ca.crt ./nl2sql-ca.crt");
        } catch (CertificateException cause) {
            throw new ApiException(0, "tls_unusable",
                    pem + " is not a PEM certificate: " + cause.getMessage());
        }
    }

    /**
     * A context trusting exactly {@code managers}, or the JVM's own when they
     * are null.
     *
     * <p>{@code protocol} is a parameter for the same reason the store type
     * above is: "TLS" is required of every JVM, so the branch that reports it
     * missing is unreachable in production and is still the branch that turns
     * a broken JVM into a sentence rather than a stack trace.
     */
    static SSLContext context(TrustManager[] managers, String protocol) {
        try {
            SSLContext context = SSLContext.getInstance(protocol);
            context.init(null, managers, null);
            return context;
        } catch (GeneralSecurityException cause) {
            throw new ApiException(0, "tls_unusable",
                    "cannot configure TLS: " + cause.getMessage());
        }
    }

    /**
     * Accepts exactly the certificates it was given, by SHA-256: the leaf a
     * server presents must be one of them. Never a CA in the chain -- a
     * chain is the server's to send, and the CA's certificate is public.
     */
    static final class TrustPinned implements X509TrustManager {

        private final java.util.Set<String> fingerprints;

        TrustPinned(String fingerprints) {
            this.fingerprints = java.util.Set.of(fingerprints.split(","));
        }

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType)
                throws CertificateException {
            check(chain);
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType)
                throws CertificateException {
            check(chain);
        }

        private void check(X509Certificate[] chain) throws CertificateException {
            if (chain == null || chain.length == 0) {
                throw new CertificateException("the server presented no certificate");
            }
            String presented = fingerprintOf(chain[0]);
            if (!fingerprints.contains(presented)) {
                throw new CertificateException(
                        "the server presented " + presented + ", which is not pinned ("
                                + String.join(", ", fingerprints) + "). The stack issues a "
                                + "server a new certificate when the names it must cover "
                                + "change, and says so at start-up; pinning the CA instead "
                                + "(--cacert) survives that.");
            }
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() {
            // A pin has no issuers: it is the leaf itself that is trusted.
            return new X509Certificate[0];
        }
    }

    /** Accepts anything. Only reachable through {@code --insecure}. */
    static final class TrustEverything implements X509TrustManager {

        @Override
        public void checkClientTrusted(X509Certificate[] chain, String authType) {
        }

        @Override
        public void checkServerTrusted(X509Certificate[] chain, String authType) {
        }

        @Override
        public X509Certificate[] getAcceptedIssuers() {
            return new X509Certificate[0];
        }
    }
}
