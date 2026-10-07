/**
 * The entry point. Mounts the application onto the page.
 *
 * Excluded from coverage: it does the one thing that only happens in a real
 * browser, and is covered by loading the built page rather than by jsdom.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { SignInGate } from "@nl2sql/web/auth/SignInGate";
import "./styles.css";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <SignInGate title="the NL2SQL SQL console" needs={["nl2sql_reviewers", "nl2sql_curators"]} group="nl2sql-reviewers or nl2sql-curators">
        <App />
      </SignInGate>
    </StrictMode>,
  );
}
