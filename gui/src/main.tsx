/**
 * The entry point. Nothing but mounting, so everything else stays testable
 * without a DOM of its own.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { SignInGate } from "./auth/SignInGate";
import "./styles.css";

const root = document.getElementById("root");
if (root) createRoot(root).render(
  <StrictMode>
    <SignInGate title="NL2SQL" needs={["nl2sql_users"]} group="nl2sql-users">
      <App />
    </SignInGate>
  </StrictMode>,
);
