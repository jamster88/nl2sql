/**
 * Series colours.
 *
 * The hexes are not chosen here -- they come from a palette whose ordering
 * was validated rather than eyeballed, on five checks: every slot inside a
 * lightness band, a chroma floor, colour-vision-deficient separation between
 * adjacent slots, a normal-vision separation floor, and contrast against the
 * surface. The order of the slots *is* the safety mechanism, so slots are
 * assigned in order and never cycled.
 *
 * Two consequences are load-bearing and easy to undo by accident:
 *
 * * **Nothing is drawn past slot 8.** A ninth series does not get a
 *   generated colour; `SERIES_CAP` folds the tail into "Other", because a
 *   colour picked to fill a gap is a colour nobody checked.
 * * **Scatter caps at 3.** Adjacent-pair validation is enough when marks sit
 *   next to each other in a known order -- bars, lines, a legend. A scatter
 *   plot puts every pair side by side at once, and under all-pairs scoring
 *   only the first three slots clear the floors in both modes.
 *
 * Three light-mode slots sit below 3:1 against the surface, which obliges
 * relief: the rows are always one click away as a table, and series of four
 * or fewer are labelled directly as well as in the legend.
 */

/** The categorical slots, in order, light mode. */
export const SERIES_LIGHT = [
  "#2a78d6", // blue
  "#eb6834", // orange
  "#1baf7a", // aqua
  "#eda100", // yellow
  "#e87ba4", // magenta
  "#008300", // green
  "#4a3aa7", // violet
  "#e34948", // red
] as const;

/** The same eight hues, stepped for the dark surface. Not an automatic flip. */
export const SERIES_DARK = [
  "#3987e5",
  "#d95926",
  "#199e70",
  "#c98500",
  "#d55181",
  "#008300",
  "#9085e9",
  "#e66767",
] as const;

/** How many series can be drawn before the tail becomes "Other". */
export const SERIES_CAP = SERIES_LIGHT.length;

/** How many can be drawn when every pair is on screen at once. */
export const SERIES_CAP_ALL_PAIRS = 3;

/**
 * The CSS custom property holding slot `index`.
 *
 * Components reference the role rather than the hex so the light and dark
 * values swap in one place -- `styles.css` defines both, and a chart drawn
 * with `var(--series-3)` is correct in either without asking which is on.
 */
export function seriesVar(index: number): string {
  return `var(--series-${(index % SERIES_CAP) + 1})`;
}

/** The label a series past the cap is folded into. */
export const OTHER_LABEL = "Other";

/**
 * Trim a list of series names to what can be drawn safely.
 *
 * Returns the names that keep their own colour. Anything past the cap is the
 * caller's to aggregate under `OTHER_LABEL`; this only decides where the
 * line falls, because the line differs by chart form.
 */
export function withinCap(names: readonly string[], allPairs = false): string[] {
  return names.slice(0, allPairs ? SERIES_CAP_ALL_PAIRS : SERIES_CAP);
}
