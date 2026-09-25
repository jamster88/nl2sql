package org.nl2sql.desktop.ui;

import javafx.scene.Node;
import javafx.scene.Parent;
import javafx.scene.control.Labeled;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.TitledPane;

import java.util.ArrayList;
import java.util.List;

/**
 * Finding things in a scene graph, so the assertions can be about behaviour.
 *
 * <p>It walks the logical tree rather than the rendered one. A
 * {@code ScrollPane} keeps its content out of {@code getChildren} until a
 * skin has been built for it, and a skin is not built until the window is
 * shown -- so a search that only followed children would report an empty
 * window for every test that did not open one.
 */
final class Nodes {

    private Nodes() {
    }

    static List<Node> withClass(Node root, String styleClass) {
        List<Node> found = new ArrayList<>();
        collect(root, styleClass, found);
        return found;
    }

    static Node oneWithClass(Node root, String styleClass) {
        List<Node> found = withClass(root, styleClass);
        if (found.size() != 1) {
            throw new AssertionError("expected one ." + styleClass + ", found " + found.size());
        }
        return found.get(0);
    }

    /** Every piece of text on show, which is what a reader would see. */
    static List<String> texts(Node root) {
        List<String> found = new ArrayList<>();
        collectText(root, found);
        return found;
    }

    static boolean says(Node root, String fragment) {
        return texts(root).stream().anyMatch(text -> text.contains(fragment));
    }

    private static void collect(Node node, String styleClass, List<Node> found) {
        if (node.getStyleClass().contains(styleClass)) {
            found.add(node);
        }
        for (Node child : children(node)) {
            collect(child, styleClass, found);
        }
    }

    /** Children, plus the content a control holds outside them. */
    private static List<Node> children(Node node) {
        List<Node> found = new ArrayList<>();
        if (node instanceof ScrollPane scroll && scroll.getContent() != null) {
            found.add(scroll.getContent());
        }
        if (node instanceof TitledPane titled && titled.getContent() != null) {
            found.add(titled.getContent());
        }
        if (node instanceof Parent parent) {
            for (Node child : parent.getChildrenUnmodifiable()) {
                if (!found.contains(child)) {
                    found.add(child);
                }
            }
        }
        return found;
    }

    private static void collectText(Node node, List<String> found) {
        if (node instanceof Labeled labeled && labeled.getText() != null
                && !labeled.getText().isEmpty() && labeled.isVisible()) {
            found.add(labeled.getText());
        }
        if (node instanceof javafx.scene.control.TextInputControl input && input.isVisible()) {
            found.add(input.getText());
        }
        for (Node child : children(node)) {
            collectText(child, found);
        }
    }
}
