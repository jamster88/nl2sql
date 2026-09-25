package org.nl2sql.desktop.ui;

import javafx.scene.control.Label;
import javafx.scene.layout.FlowPane;
import javafx.scene.layout.Region;
import org.nl2sql.desktop.api.Models;

import java.util.List;

/**
 * What this is connected to.
 *
 * <p>Small, and at the bottom, because it is reference rather than news -- but
 * present, because "which model answered this" and "how many tables are in
 * scope" are the first two questions asked of any answer that looks wrong, and
 * an interface that cannot say is an interface people stop trusting.
 *
 * <p>It carries one thing the web interface does not have to: how the server's
 * certificate was checked. A browser decides that for itself and shows a
 * padlock; this application was told, by a flag, and the answer can be "not at
 * all" -- so it says which, for as long as it is true.
 *
 * <p>The failure case earns more room than the success case: a client that
 * cannot reach its API should say so in words, since every other part of the
 * window will simply look empty.
 */
public final class StatusBar {

    private final FlowPane node = new FlowPane();
    private final String connection;
    private List<String> warnings = List.of();
    private Models.Meta meta;

    /** @param connection how the certificate is being checked, from {@code Tls.describe} */
    public StatusBar(String connection) {
        this.connection = connection;
        node.getStyleClass().add("status");
        node.setHgap(12);
        node.setVgap(4);
        connecting();
    }

    public Region node() {
        return node;
    }

    public void connecting() {
        meta = null;
        node.getChildren().setAll(muted("Connecting…"));
    }

    public void show(Models.Meta value) {
        this.meta = value;
        redraw();
    }

    public void setWarnings(List<String> values) {
        this.warnings = List.copyOf(values);
        redraw();
    }

    /** Replace everything with why the server could not be reached. */
    public void showError(String message) {
        meta = null;
        Label headline = new Label("Not connected.");
        headline.getStyleClass().add("status-error");
        node.getChildren().setAll(
                headline,
                new Label(message),
                muted("Check that the API is running — ./launch.sh --api"));
    }

    private void redraw() {
        if (meta == null) {
            // Warnings arrived before meta did, or after a failure replaced
            // it. Either way there is nothing to hang them on yet.
            return;
        }
        Label service = new Label(meta.service() + " " + meta.version());
        service.getStyleClass().add("status-service");
        node.getChildren().setAll(
                service,
                muted(meta.model()),
                muted(meta.tables().size() + (meta.tables().size() == 1 ? " table" : " tables")),
                muted(meta.authentication().equals("bearer") ? "token required" : "no token"),
                muted(connection),
                muted(meta.feedback() ? "feedback staged for review" : "feedback kept locally"));
        if (!meta.scope().isEmpty()) {
            node.getChildren().add(muted(meta.scope()));
        }
        for (String warning : warnings) {
            Label label = new Label(warning);
            label.getStyleClass().add("status-warning");
            node.getChildren().add(label);
        }
    }

    private static Label muted(String text) {
        Label label = new Label(text);
        label.getStyleClass().add("muted");
        return label;
    }
}
