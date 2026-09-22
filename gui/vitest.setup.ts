/**
 * jsdom lays every element out at zero size and implements no layout, which
 * is fine here: the charts draw into a fixed `viewBox` and let the SVG scale
 * itself, so what they render does not depend on a measured width. Nothing
 * needs stubbing for them.
 *
 * `EventSource` is stubbed per test instead of here, in `test/helpers.ts`.
 * It is the thing under test in several places, and one shared fake would
 * hide the reconnect and fallback paths rather than exercise them.
 */

import "@testing-library/jest-dom/vitest";
