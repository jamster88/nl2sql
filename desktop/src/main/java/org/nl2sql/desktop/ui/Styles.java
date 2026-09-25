package org.nl2sql.desktop.ui;

import javafx.css.PseudoClass;
import javafx.scene.Scene;

/**
 * The stylesheet, and the two states that cannot be expressed as class names.
 *
 * <p>A JavaFX pseudo-class is toggled from code and styled from CSS, which is
 * what a state wants: the alternative is adding and removing a style class by
 * hand and eventually removing the wrong one.
 */
public final class Styles {

    /** A cell a claim was read from, while that claim is pointed at. */
    public static final PseudoClass HIGHLIGHTED = PseudoClass.getPseudoClass("highlighted");

    /** A verdict button that is the recorded verdict. */
    public static final PseudoClass CHOSEN = PseudoClass.getPseudoClass("chosen");

    private static final String SHEET = "/org/nl2sql/desktop/nl2sql.css";

    private Styles() {
    }

    /** Dress a scene. Returns it, so it can be written in one expression. */
    public static Scene apply(Scene scene) {
        scene.getStylesheets().add(url());
        return scene;
    }

    /** Where the stylesheet is, as the toolkit wants it. */
    public static String url() {
        return url(SHEET);
    }

    /**
     * The same, for any resource.
     *
     * <p>Split out so the missing case can be reached from a test. A jar built
     * without its resources is a broken build rather than a missing file, so
     * it will not happen -- but a branch nothing can take is a branch nobody
     * can be sure of, and this one decides whether the window has a style
     * sheet or an exception.
     */
    static String url(String resource) {
        java.net.URL found = Styles.class.getResource(resource);
        if (found == null) {
            throw new IllegalStateException("this build has no " + resource + " in it");
        }
        return found.toExternalForm();
    }
}
