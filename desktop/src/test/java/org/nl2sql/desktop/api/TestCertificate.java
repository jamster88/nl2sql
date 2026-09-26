package org.nl2sql.desktop.api;

import org.junit.jupiter.api.Assumptions;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/**
 * A self-signed certificate for {@code localhost}, made on the spot.
 *
 * <p>Made rather than committed. A private key in a repository is a private
 * key in every clone, every fork and every secret scanner's report, and the
 * fact that this one only ever signs a loopback socket in a test is not
 * something a scanner -- or a person skimming a diff -- can tell. The Python
 * side generates its certificate at test time for the same reason
 * ({@code tests/api/test_live_tls.py}).
 *
 * <p>{@code keytool} does the work because it is part of every JDK, so this
 * needs nothing installed that running the tests did not already need.
 */
final class TestCertificate {

    static final String PASSWORD = "changeit";

    private static Path directory;

    private TestCertificate() {
    }

    /** The keystore a test server is started with, and the PEM a client trusts. */
    static synchronized Path keystore() {
        make();
        return directory.resolve("server.p12");
    }

    static synchronized Path pem() {
        make();
        return directory.resolve("server.crt");
    }

    private static void make() {
        if (directory != null) {
            return;
        }
        Path scratch;
        try {
            scratch = Files.createTempDirectory("nl2sql-desktop-tls");
            scratch.toFile().deleteOnExit();
        } catch (IOException cause) {
            throw new IllegalStateException("cannot make somewhere to put a test certificate", cause);
        }
        Path keytool = Path.of(System.getProperty("java.home"), "bin", "keytool");
        Assumptions.assumeTrue(Files.isExecutable(keytool), "no keytool in this JDK");
        Path store = scratch.resolve("server.p12");
        Path certificate = scratch.resolve("server.crt");
        store.toFile().deleteOnExit();
        certificate.toFile().deleteOnExit();
        run(List.of(keytool.toString(), "-genkeypair", "-alias", "nl2sql",
                "-keyalg", "RSA", "-keysize", "2048", "-validity", "3650",
                "-dname", "CN=localhost,O=nl2sql desktop tests",
                "-ext", "SAN=dns:localhost,ip:127.0.0.1",
                "-keystore", store.toString(), "-storetype", "PKCS12",
                "-storepass", PASSWORD, "-keypass", PASSWORD));
        run(List.of(keytool.toString(), "-exportcert", "-rfc", "-alias", "nl2sql",
                "-keystore", store.toString(), "-storepass", PASSWORD,
                "-file", certificate.toString()));
        directory = scratch;
    }

    private static void run(List<String> command) {
        try {
            Process process = new ProcessBuilder(command).redirectErrorStream(true).start();
            String output = new String(process.getInputStream().readAllBytes());
            if (process.waitFor() != 0) {
                throw new IllegalStateException("keytool failed: " + output);
            }
        } catch (IOException cause) {
            throw new IllegalStateException("cannot run keytool", cause);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted running keytool", interrupted);
        }
    }
}
