/**
 * What can hold gamepad focus, where a page's focus lands when it opens, and how
 * it is put there.
 *
 * Steam's gamepad navigation keeps a focus pointer across a page swap and
 * resolves it on the next input — onto whatever sits at the old page's
 * position — so a newly mounted body has to claim focus itself. Three callers
 * do, on the same delay: the wide-page frame for its own body
 * (`src/components/qam/WidePage.tsx`), the Sync page's run view for the body it
 * swaps in mid-page (`src/components/sync/RunPanel.tsx`), and the panel's router
 * for the pages that place none of their own (`src/index.tsx`).
 *
 * The first two take {@link firstBodyStop} as it stands. The router takes
 * {@link pageEntryStop}, which lets the page it has just mounted name the area
 * focus belongs in and otherwise answers exactly the same thing.
 */

/**
 * Every shape Steam gives a focus stop, measured in the running QAM rather than
 * assumed: its own components render `div[tabindex="0"]` (`Focusable`, a toggle
 * row, a table row carrying an activate handler) and a `DialogButton` is a
 * native `button` with no tabindex attribute at all.
 *
 * The two selectors below are joined from this one list rather than written out
 * twice, so a shape added here reaches both.
 */
const FOCUS_STOP_SHAPES = ["[tabindex]", "button", "a[href]", "input", "select", "textarea"];

/**
 * Anything focus can land on, disabled controls included.
 *
 * A wide page keeps its buttons rendered and disabled rather than hidden, and
 * the injected sheet gives them Steam's focus outline, so the stick lands on
 * them and the row stays walkable — which is why `ScrollRegion` reads this one,
 * and the entry-focus finder below takes its candidates from the narrower one
 * while testing containment against this.
 */
export const FOCUS_STOPS = FOCUS_STOP_SHAPES.join(", ");

// The same shapes, less anything disabled: what entry focus may land ON.
const ENTRY_STOPS = FOCUS_STOP_SHAPES.map((shape) => `${shape}:not([disabled])`).join(", ");

/**
 * The stop a page opens on: the first enabled thing in document order that can
 * take focus and contains no focus stop of its own.
 *
 * One rule for both widths. Document order, never "the first button", because a
 * page's first button is not its first row, and it is not that on either width.
 * On a wide list-and-detail page whose list rows carry no control of their own,
 * the first button in the body is in the DETAIL pane, so a button-first rule
 * opens the page somewhere inside the detail and moves as the detail's content
 * changes. On a narrow page the same rule fails from the other end: a status row
 * states rather than acts and stays focusable all the same, so the first BUTTON
 * is whatever action happens to be on screen — a notice's own button, or the
 * menu at the very bottom of the panel — so where the page opened would depend
 * on which condition was showing. A page wanting somewhere other than its first
 * row says so ({@link ENTRY_STOP_ATTR}) rather than being guessed at.
 *
 * Innermost, because a container `Focusable` carries `tabindex="0"` of its own
 * and precedes in document order every row it wraps: taking the first match
 * lands focus on the container and leaves the reader a step away from the row.
 *
 * **The two halves read different selectors on purpose.** A candidate must be
 * enabled, but a container is skipped for holding a stop of ANY kind: a button
 * row whose every button is disabled is still a container Steam's navigation
 * does not stop on, so focusing it would put the reader nowhere and take the
 * ring off the next real row. The test cannot tell that row from one carrying an
 * activate handler, so it skips both; a row whose only inner stop is disabled is
 * stepped over, which no body's first column produces today. Where that leaves
 * no candidate — no enabled stop that is free of stops inside it — nothing is
 * placed and the page keeps whatever Steam's retained pointer resolves to.
 */
export function firstBodyStop(root: ParentNode): HTMLElement | null {
  const stops = [...root.querySelectorAll<HTMLElement>(ENTRY_STOPS)];
  return stops.find((stop) => stop.querySelector(FOCUS_STOPS) === null) ?? null;
}

/**
 * Marks the part of a page entry focus belongs in — a declaration, read by the
 * panel's router (`src/index.tsx`) and by nothing else.
 *
 * It is the other half of `WidePage`'s `OWNS_ENTRY_FOCUS_ATTR` rather than a
 * second spelling of it, which is why it is a second attribute: that one tells
 * the router to place NOTHING, because the page places its own; this one tells
 * it WHERE. Carrying both would ask the router to honour a declaration it has
 * already been told to stay out of, and no page carries both.
 *
 * It names an AREA, not the stop: {@link firstBodyStop} still picks the element
 * inside it, so one rule decides which node takes focus wherever focus is
 * placed. The area is trusted rather than validated — a declaration around
 * nothing focusable falls back to the body, which is where the page would have
 * opened anyway.
 */
export const ENTRY_STOP_ATTR = "data-romm-entry-stop";

/**
 * Long enough to land after Steam has finished mounting the page and resolving
 * its own focus pointer. The same 50 ms the scroll helpers use.
 */
export const ENTRY_FOCUS_DELAY_MS = 50;

/**
 * The stop the panel's router opens a page on: inside the page's own
 * declaration where it makes one, and the first stop of the whole body
 * otherwise.
 *
 * Main is the only page that declares one, on the menu's Sync entry — its three
 * status rows act on nothing, so opening on the first of them spends the
 * reader's first press moving to what they came for. The cost of that is
 * Steam's: it scrolls the focused element into view, so on a Main tall enough to
 * scroll the panel opens part-way down. Stated in
 * `docs/architecture/qam-panel.md`, section Main, and deliberately not defended
 * against here — a rule that opened somewhere else depending on what is on
 * screen is the thing this declaration replaced.
 */
export function pageEntryStop(root: ParentNode): HTMLElement | null {
  const declared = root.querySelector<HTMLElement>(`[${ENTRY_STOP_ATTR}]`);
  return (declared === null ? null : firstBodyStop(declared)) ?? firstBodyStop(root);
}

/**
 * Put entry focus on the stop `findStop` picks out of `root`, and answer whether
 * there was one.
 *
 * `gpfocus` is the class Steam's own navigation adds to the focused element —
 * `.focus()` alone moves the DOM focus and leaves the element undrawn, so both
 * halves are needed for the reader to see where they are.
 */
export function placeEntryFocus(root: ParentNode, findStop: (root: ParentNode) => HTMLElement | null): boolean {
  const stop = findStop(root);
  if (!stop) return false;
  stop.focus();
  stop.classList.add("gpfocus");
  return true;
}
