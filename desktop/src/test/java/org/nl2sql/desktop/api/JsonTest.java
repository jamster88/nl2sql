package org.nl2sql.desktop.api;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class JsonTest {

    @Test
    void something_that_is_not_json_is_a_failure_a_user_can_read() {
        // The usual cause is a proxy's HTML error page arriving where the API
        // should have been, and a Jackson stack trace says nothing about that.
        ApiException failed = assertThrows(ApiException.class,
                () -> Json.read("<html>502 Bad Gateway</html>", Models.Health.class));

        assertEquals("malformed_response", failed.code());
        assertEquals(0, failed.status());
        assertTrue(failed.retryable());
    }

    @Test
    void a_tree_that_is_not_json_fails_the_same_way() {
        assertEquals("malformed_response",
                assertThrows(ApiException.class, () -> Json.tree("not json")).code());
    }

    @Test
    void a_node_can_be_turned_into_a_model() {
        // How the event stream gets a job: the envelope is parsed first, the
        // payload second.
        assertEquals("x", Json.convert(Json.tree("{\"id\": \"x\"}"), Models.Job.class).id());
    }

    @Test
    void a_node_of_the_wrong_shape_is_a_failure_rather_than_a_silent_default() {
        assertEquals("malformed_response", assertThrows(ApiException.class,
                () -> Json.convert(Json.tree("[1, 2, 3]"), Models.Job.class)).code());
    }

    @Test
    void writing_something_unserialisable_says_what_it_was() {
        // Unreachable from this application, whose requests are records of
        // strings and numbers, but an unchecked throw beats a checked
        // exception on every call site.
        Object unserialisable = new Object() {
            @SuppressWarnings("unused")
            public Object getSelf() {
                throw new UnsupportedOperationException("no");
            }
        };
        assertThrows(IllegalArgumentException.class, () -> Json.write(unserialisable));
    }

    @Test
    void the_ordinary_cases_work() {
        assertEquals("[1,2]", Json.write(List.of(1, 2)));
        assertEquals("{}", Json.write(Map.of()));
        assertEquals(2, Json.tree("[1, 2]").size());
    }
}
