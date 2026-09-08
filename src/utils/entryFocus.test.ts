/**
 * entryFocus tests — which stop the rule picks, and what placing focus on it
 * leaves behind.
 *
 * happy-dom has no gamepad and no nav tree, so what is pinned is the CHOICE of
 * element and the two marks Steam's own navigation leaves on it; that the reader
 * then sees the focus ring is the device round's to settle.
 */

import { describe, it, expect, afterEach } from "vitest";
import { firstBodyStop, placeEntryFocus } from "./entryFocus";

function page(html: string): HTMLElement {
  const root = document.createElement("div");
  root.innerHTML = html;
  document.body.append(root);
  return root;
}

// Each root is appended to the document so `.focus()` has somewhere real to put
// focus; without this the next test starts on the last one's leftovers.
afterEach(() => {
  document.body.replaceChildren();
});

describe("firstBodyStop", () => {
  it("takes the innermost stop in document order, not the first button", () => {
    // The shape of a list-and-detail body whose list rows carry no control: the
    // first button in the body is in the DETAIL pane, so a button-first rule
    // would open the page inside the detail.
    const root = page(`
      <div tabindex="0" id="list">
        <div tabindex="0" id="row">General</div>
      </div>
      <div tabindex="0" id="detail"><button id="action">Do the thing</button></div>
    `);

    expect(firstBodyStop(root)?.id).toBe("row");
  });

  it("skips a disabled control and takes the next stop", () => {
    // A page opening with focus on a dead control says nothing about where the
    // reader is.
    const root = page(`<button id="dead" disabled>Download</button><button id="live">Delete</button>`);

    expect(firstBodyStop(root)?.id).toBe("live");
  });

  it("skips a container whose stops are all disabled, and takes the next row", () => {
    // The shape a pane really produces: a button row whose every button is
    // disabled. It carries `tabindex="0"` but is a container Steam's navigation
    // does not stop on, so taking the DOM focus and the ring there would put the
    // reader nowhere and take both off the next real row. A candidate must be
    // enabled; a container is skipped for holding a stop of any kind.
    const root = page(`
      <div tabindex="0" id="buttons">
        <button disabled>Download required</button>
        <button disabled>Download all</button>
      </div>
      <div tabindex="0" id="row">Nintendo 64</div>
    `);

    expect(firstBodyStop(root)?.id).toBe("row");
  });

  it("takes a button over a container that wraps it", () => {
    const root = page(`<div tabindex="0" id="wrapper"><button id="btn">go</button></div>`);

    expect(firstBodyStop(root)?.id).toBe("btn");
  });

  it("answers nothing for a body with no stop at all", () => {
    const root = page(`<div>just words</div>`);

    expect(firstBodyStop(root)).toBeNull();
  });

  it("answers nothing where every stop is disabled", () => {
    // The page then keeps whatever focus Steam's retained pointer resolves to,
    // which is the state this whole helper exists to replace — worth pinning as
    // a known edge rather than discovering it on a device.
    const root = page(`<button id="dead" disabled>Download</button>`);

    expect(firstBodyStop(root)).toBeNull();
  });

  it("takes a stating row over the first button below it", () => {
    // The shape of a narrow page: Main's status rows act on nothing and stay
    // focusable all the same, so its first BUTTON is the menu at the bottom of
    // the panel. A button-first rule opens the page with the status it leads
    // with already scrolled off the top.
    const root = page(`
      <div tabindex="0" id="connection">Connection</div>
      <div tabindex="0" id="last-sync">Last sync</div>
      <button id="menu-sync">Sync</button>
    `);

    expect(firstBodyStop(root)?.id).toBe("connection");
  });
});

describe("placeEntryFocus", () => {
  it("focuses the stop its finder picks and marks it the way Steam does", () => {
    const root = page(`<button id="btn">go</button>`);

    expect(placeEntryFocus(root, firstBodyStop)).toBe(true);
    // `.focus()` alone moves DOM focus and leaves the element undrawn: the class
    // is what Steam's own navigation adds, and what the focus ring keys on.
    expect(root.querySelector("#btn")).toHaveFocus();
    expect(root.querySelector("#btn")).toHaveClass("gpfocus");
  });

  it("reports that it placed nothing when the finder answers nothing", () => {
    const root = page(`<div>just words</div>`);

    expect(placeEntryFocus(root, firstBodyStop)).toBe(false);
  });
});
