package org.nl2sql.desktop.api;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

/**
 * JSON, configured once.
 *
 * <p>The one setting that matters is {@code FAIL_ON_UNKNOWN_PROPERTIES}, off.
 * A client that refuses a document because the server has learned to send one
 * more field is a client that breaks on the next release of a service it does
 * not control -- and every field this application reads is one it asked for by
 * name, so an unread one costs nothing.
 *
 * <p>The converse, a field the server stopped sending, is not defended against
 * here: it arrives as null and {@link Models} fills it in. That split is
 * deliberate. Tolerating a surplus is free; tolerating a shortfall has to be
 * decided per field, and {@code Models} is where those decisions are written.
 */
public final class Json {

    private static final ObjectMapper MAPPER = new ObjectMapper()
            .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .disable(SerializationFeature.FAIL_ON_EMPTY_BEANS);

    private Json() {
    }

    /** Parse, turning any parser failure into the one exception type callers handle. */
    public static <T> T read(String body, Class<T> type) {
        try {
            return MAPPER.readValue(body, type);
        } catch (JsonProcessingException cause) {
            throw new ApiException(0, "malformed_response",
                    "the server sent something that is not the expected JSON: " + cause.getOriginalMessage());
        }
    }

    /** Parse without knowing the shape, for the error envelope and the event stream. */
    public static JsonNode tree(String body) {
        try {
            return MAPPER.readTree(body);
        } catch (JsonProcessingException cause) {
            throw new ApiException(0, "malformed_response",
                    "the server sent something that is not JSON: " + cause.getOriginalMessage());
        }
    }

    public static String write(Object value) {
        try {
            return MAPPER.writeValueAsString(value);
        } catch (JsonProcessingException cause) {
            // Everything written here is a record of strings and numbers, so
            // this is unreachable in practice -- but an unchecked rethrow is
            // still better than a checked exception on every call site.
            throw new IllegalArgumentException("cannot serialise " + value, cause);
        }
    }

    /** Convert an already-parsed node, which is how the event stream gets a job. */
    public static <T> T convert(JsonNode node, Class<T> type) {
        try {
            return MAPPER.treeToValue(node, type);
        } catch (JsonProcessingException cause) {
            throw new ApiException(0, "malformed_response",
                    "the server sent an event this client cannot read: " + cause.getOriginalMessage());
        }
    }
}
