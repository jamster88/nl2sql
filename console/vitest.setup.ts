/**
 * jsdom implements no layout and no clipboard. Nothing here depends on the
 * first; the second is stubbed per test, where the copy button that uses it
 * is the thing under test.
 */

import "@testing-library/jest-dom/vitest";
