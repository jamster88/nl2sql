package org.nl2sql.desktop.api;

import org.junit.jupiter.api.Test;
import org.nl2sql.desktop.Fakes;

import java.io.IOException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class EventStreamTest {

    private static List<EventStream.Event> read(String... lines) throws IOException {
        List<EventStream.Event> seen = new ArrayList<>();
        EventStream.read(new Fakes.ListLines(new ArrayDeque<>(List.of(lines))), seen::add);
        return seen;
    }

    @Test
    void an_event_is_a_name_a_payload_and_an_id() throws IOException {
        List<EventStream.Event> seen = read(
                "id: 4",
                "event: progress",
                "data: {\"seq\":4}",
                "");

        assertEquals(1, seen.size());
        assertEquals(new EventStream.Event("progress", "{\"seq\":4}", "4"), seen.get(0));
    }

    @Test
    void repeated_data_lines_are_joined_rather_than_replaced() throws IOException {
        // What the specification says, and a long answer will eventually be
        // split across lines by something in the middle.
        assertEquals("{\n\"a\": 1\n}", read("data: {", "data: \"a\": 1", "data: }", "").get(0).data());
    }

    @Test
    void a_comment_is_the_keep_alive_and_carries_nothing() throws IOException {
        // Sent every API_KEEPALIVE_SECONDS so a proxy does not close an idle
        // connection while the model thinks.
        assertEquals(List.of(), read(": keep-alive", "", ": another", ""));
    }

    @Test
    void an_event_with_no_data_is_dropped_rather_than_delivered_empty() throws IOException {
        assertEquals(List.of(), read("event: ping", ""));
    }

    @Test
    void the_name_defaults_to_message_and_resets_after_each_event() throws IOException {
        List<EventStream.Event> seen = read(
                "event: progress", "data: one", "",
                "data: two", "");

        assertEquals("progress", seen.get(0).name());
        assertEquals("message", seen.get(1).name());
        assertEquals("", seen.get(1).id());
    }

    @Test
    void one_space_after_the_colon_is_framing_and_a_second_is_data() throws IOException {
        assertEquals("  indented", read("data:   indented", "").get(0).data());
    }

    @Test
    void a_field_with_no_colon_at_all_is_a_field_with_an_empty_value() throws IOException {
        assertEquals("", read("data", "").get(0).data());
    }

    @Test
    void a_field_this_client_does_not_know_is_ignored() throws IOException {
        // `retry:` is in the specification and this client does not use it.
        assertEquals(1, read("retry: 5000", "data: x", "").size());
    }

    @Test
    void an_event_the_server_never_finished_is_not_delivered() throws IOException {
        // The stream was cut mid-event. Half a JSON document is worse than
        // none, and the watcher treats the silent close as a break anyway.
        assertEquals(List.of(), read("event: done", "data: {\"id\""));
    }

    @Test
    void a_reader_that_fails_mid_stream_says_so() {
        ApiClient.Lines broken = new ApiClient.Lines() {
            @Override
            public String readLine() throws IOException {
                throw new IOException("connection reset");
            }

            @Override
            public void close() {
            }
        };

        assertEquals("connection reset", assertThrows(IOException.class,
                () -> EventStream.read(broken, event -> {
                })).getMessage());
    }


    @Test
    void a_value_with_no_space_after_the_colon_keeps_all_of_itself() throws IOException {
        assertEquals("tight", read("data:tight", "").get(0).data());
    }
}
