package org.nl2sql.desktop.api;

/**
 * Signing a person in, and out.
 *
 * <p>An interface for the reason {@link ApiClient} is one: the window is
 * driven against a fake in its tests, with no auth service and no directory
 * behind it.
 */
public interface SignIn {

    /**
     * Sign in. From then on every call to the API presents this session.
     *
     * @throws ApiException when the user name and password are refused, the
     *                      service is unreachable, or it is not answering
     */
    Models.Token signIn(String username, String password);

    /**
     * Forget the session, and end it at the auth service, so a copy of the
     * token stops working too. The window is signed out whatever the
     * service answers.
     */
    void signOut();
}
