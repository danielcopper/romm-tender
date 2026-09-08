/**
 * What can hold gamepad focus, where a page's focus lands when it opens, and how
 * it is put there.
 *
 * Steam's gamepad navigation keeps a focus pointer across a page swap and
 * resolves it on the next input — onto whatever sits at the old page's
 * position — so a newly mounted body has to claim focus itself. Three callers
 * do, on the same delay: the wide-page frame for its own body
 * (`src/components/qam/WidePage.tsx`), the panel's router for the pages that
 * place none of their own (`src/index.tsx`), and
 * {@link useEntryFocusOnBodySwap} for a page whose body changes under the reader
 * while the page stays open — the Sync page's left column.
 *
 * The frame and the swap take {@link firstBodyStop} as it stands. The router
 * takes {@link pageEntryStop}, which lets the page it has just mounted name the
 * area focus belongs in and otherwise answers exactly the same thing.
 */

import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";

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

/**
 * Follow a body that swaps under the reader — `body` holds whichever one is
 * showing and `bodyKind` names it — but only where the swap took the reader's
 * focus with it.
 *
 * The swap unmounts the control the reader was standing on, and Steam resolves
 * its retained pointer onto whatever now sits at that position, so their next
 * press goes somewhere nobody chose. The incoming body therefore claims focus
 * the way a newly mounted one does: {@link firstBodyStop} inside it, on
 * {@link ENTRY_FOCUS_DELAY_MS}.
 *
 * **Only where the swap took it.** A reader standing anywhere else — another
 * column, the frame's Back row — put themselves there, and a body changing
 * behind them is no reason to move them. So the question is asked of the
 * element that was standing in THIS body, held here as focus moves through it:
 * the swap disconnects it, and a reader who had walked out of the body left
 * nothing behind to disconnect.
 *
 * **Which element holds focus after the swap cannot answer that**, which is why
 * this keeps a note of its own. A swap is not the only thing that can take
 * focus in one commit — Force Full Sync ends the pending preview and goes dead
 * in the same one, and that is exactly the case that must be left alone —
 * whereas what stood in the body is a fact this page owns rather than a reading
 * of what the browser did with a control elsewhere.
 *
 * Focus leaving for somewhere real is the reader choosing; focus landing on
 * nothing (`relatedTarget` null) is what a removal leaves behind, so the note
 * survives it and is spent by the swap it belongs to.
 *
 * The note is read in the commit that swapped the body, before anything can
 * move focus into what replaced it — hence a layout effect.
 *
 * **A body can swap twice inside the delay, and the placement follows the last
 * one.** The Sync page's preview path does exactly that: the backend's own
 * "Preview ready" frame stops the run before the `sync_preview` callable
 * answers, so the column goes run → idle → preview in two commits milliseconds
 * apart. Each commit's cleanup cancels the placement the one before it
 * scheduled, so a note spent by the FIRST of them leaves the last swap nothing
 * to answer and the reader with no focus at all — which is what the device
 * showed (#1814). Hence the two halves below: a note is spent by the placement
 * it causes rather than by a swap that merely observes it, and the timer asks
 * which body it is landing in when it fires rather than when it was set.
 *
 * **Nothing here is asked of a module global**, neither the document nor the
 * window: every question goes to a node this page holds — `root.contains`, the
 * event's own target, `isConnected`. Plugin code runs in the SharedJSContext
 * window while these nodes belong to the QAM view's own document, so a
 * `document.activeElement` read here would answer about the wrong document, and
 * no test in this repo could see it: happy-dom has one realm.
 *
 * **The mount is not a swap**, and nothing is placed on it: the frame opens the
 * page (`WidePage`), and a body placing focus there would be a second placement
 * racing the frame's own for the same element.
 */
export function useEntryFocusOnBodySwap(body: RefObject<HTMLElement | null>, bodyKind: string): void {
  const stoodOn = useRef<Element | null>(null);
  const shown = useRef(bodyKind);

  useEffect(() => {
    const root = body.current;
    if (root === null) return;
    const took = (event: FocusEvent) => {
      stoodOn.current = event.target as Element | null;
    };
    const left = (event: FocusEvent) => {
      const next = event.relatedTarget as Node | null;
      if (next !== null && !root.contains(next)) stoodOn.current = null;
    };
    root.addEventListener("focusin", took);
    root.addEventListener("focusout", left);
    return () => {
      root.removeEventListener("focusin", took);
      root.removeEventListener("focusout", left);
    };
  }, [body]);

  useLayoutEffect(() => {
    const previous = shown.current;
    shown.current = bodyKind;
    if (body.current === null || previous === bodyKind) return;
    const stop = stoodOn.current;
    if (stop === null || stop.isConnected) return;
    const timer = setTimeout(() => {
      const root = body.current;
      if (root === null) return;
      // Spent by the placement it causes, and only then — a swap merely
      // OBSERVES that focus was taken. Clearing it here rather than after
      // `placeEntryFocus` is what lets that call's own `focusin` re-arm the note
      // on the stop it lands on; a body with nothing to land on caused no
      // placement, so the note goes back and the next swap asks again.
      stoodOn.current = null;
      if (!placeEntryFocus(root, firstBodyStop)) stoodOn.current = stop;
    }, ENTRY_FOCUS_DELAY_MS);
    return () => clearTimeout(timer);
  }, [body, bodyKind]);
}
