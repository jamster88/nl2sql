package org.nl2sql.desktop;

import javafx.application.Application;
import org.nl2sql.desktop.ui.DesktopApp;

/**
 * The entry point, which deliberately does not extend {@code Application}.
 *
 * <p>Two reasons, and the second is the one that bites. A class that extends
 * {@code Application} and is launched from the class path makes JavaFX refuse
 * to start with a message about missing runtime components; a launcher that
 * does not is the documented way round it, and it is what lets this ship as a
 * single jar that runs anywhere with {@code java -jar}.
 *
 * <p>The first reason is plainer: the settings are checked here, before the
 * toolkit is started, so a mistyped flag is one line on stderr and an exit
 * code rather than a JavaFX exception dialog about an exception in
 * {@code start()}.
 */
public final class Main {

    private Main() {
    }

    public static void main(String[] args) {
        if (Settings.wantsHelp(args)) {
            System.out.println(Settings.usage());
            return;
        }
        try {
            Settings.from(System.getenv(), args);
        } catch (IllegalArgumentException wrong) {
            System.err.println(wrong.getMessage());
            System.exit(2);
            return;
        }
        Application.launch(DesktopApp.class, args);
    }
}
