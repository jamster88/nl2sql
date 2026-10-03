/**
 * The entry point. Mounts the application onto the page.
 *
 * Excluded from coverage: it does the one thing that only happens in a real
 * browser, and is covered by the container test that loads the built page
 * rather than by jsdom.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (root) createRoot(root).render(<StrictMode><App /></StrictMode>);
