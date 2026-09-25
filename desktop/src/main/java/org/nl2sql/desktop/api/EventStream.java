package org.nl2sql.desktop.api;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

/**
 * Server-sent events, taken apart.
 *
 * <p>A browser has {@code EventSource} and a Java client does not, so the
 * framing is here: fields separated by colons, events separated by blank
 * lines, lines beginning with a colon being comments the server sends to stop
 * a proxy closing an idle connection. That is the whole format.
 *
 * <p>Two details matter and both come from what the API does with them. Repeated
 * {@code data:} lines are joined with newlines rather than the last one
 * winning, because that is what the specification says and a long answer will
 * eventually be split. And an event with no {@code data:} at all is dropped
 * rather than delivered empty -- the keep-alive comment is the common case, and
 * a consumer that had to filter those would be filtering them in four places.
 */
public final class EventStream {

    /** One event: what it was called, what it carried, and where to resume from. */
    public record Event(String name, String data, String id) {
    }

    private EventStream() {
    }

    /**
     * Read until the stream ends, handing every complete event to {@code sink}.
     *
     * <p>Returns when the server closes the connection or the reader is closed
     * from another thread; it does not reconnect. Deciding whether a closed
     * stream is the end of the story is {@link JobWatcher}'s job, and it needs
     * more than this function knows to decide it.
     */
    public static void read(ApiClient.Lines lines, Consumer<Event> sink) throws IOException {
        List<String> data = new ArrayList<>();
        String name = "message";
        String id = "";

        String line;
        while ((line = lines.readLine()) != null) {
            if (line.isEmpty()) {
                if (!data.isEmpty()) {
                    sink.accept(new Event(name, String.join("\n", data), id));
                }
                data.clear();
                name = "message";
                id = "";
                continue;
            }
            if (line.startsWith(":")) {
                continue;
            }
            int colon = line.indexOf(':');
            String field = colon < 0 ? line : line.substring(0, colon);
            // One optional space after the colon belongs to the framing, not
            // to the value. A second one is part of the data.
            String value = colon < 0 ? "" : strip(line.substring(colon + 1));
            switch (field) {
                case "event" -> name = value;
                case "data" -> data.add(value);
                case "id" -> id = value;
                default -> {
                    // A field this client does not know, such as `retry`.
                    // The specification says to ignore it.
                }
            }
        }
    }

    private static String strip(String value) {
        return value.startsWith(" ") ? value.substring(1) : value;
    }
}
