/**
 * The entry point. Nothing but mounting, so everything else stays testable
 * without a DOM of its own.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (root) createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
