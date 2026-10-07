package org.nl2sql.desktop.api;

import org.junit.jupiter.api.Test;
import org.nl2sql.desktop.Fakes;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;

class SessionTest {

    @Test
    void with_nobody_signed_in_the_static_token_is_presented_or_nothing() {
        assertEquals("s3cret", new Session("s3cret").bearer());
        assertEquals("", new Session("").bearer());
        assertNull(new Session("s3cret").current());
    }

    @Test
    void a_person_who_signed_in_outranks_the_static_token_until_they_sign_out() {
        Session session = new Session("s3cret");
        Models.Token token = Fakes.token("ada", "Ada Lovelace");

        session.signedIn(token);

        assertSame(token, session.current());
        assertEquals("eyJ.session.ada", session.bearer());

        session.signOut();

        assertEquals("s3cret", session.bearer());
    }
}
