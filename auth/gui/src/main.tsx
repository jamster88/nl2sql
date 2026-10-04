/**
 * The entry point: the page, behind sign-in, for nl2sql-admins only.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { SignInGate } from "./auth/SignInGate";
import "./styles.css";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <SignInGate title="the NL2SQL directory" needs={["nl2sql_admins"]} group="nl2sql-admins">
        <App />
      </SignInGate>
    </StrictMode>,
  );
}
