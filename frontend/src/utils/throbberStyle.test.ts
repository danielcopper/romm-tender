import { afterEach, describe, expect, it } from "vitest";
import { THROBBER_STYLE_ID, ensureThrobberStyle } from "./throbberStyle";

describe("ensureThrobberStyle", () => {
  afterEach(() => document.getElementById(THROBBER_STYLE_ID)?.remove());

  it("puts the throbber's spin into the document it is handed", () => {
    ensureThrobberStyle(document);
    const css = document.getElementById(THROBBER_STYLE_ID)?.textContent ?? "";
    expect(css).toMatch(/\.romm-throbber\s*\{[^}]*animation:\s*romm-spin/);
    expect(css).toContain("@keyframes romm-spin");
  });

  it("puts it there once, however often a throbber mounts", () => {
    ensureThrobberStyle(document);
    ensureThrobberStyle(document);
    expect(document.querySelectorAll(`#${THROBBER_STYLE_ID}`)).toHaveLength(1);
  });

  it("leaves every other document alone", () => {
    const other = document.implementation.createHTMLDocument("other");
    ensureThrobberStyle(other);
    expect(other.getElementById(THROBBER_STYLE_ID)).not.toBeNull();
    expect(document.getElementById(THROBBER_STYLE_ID)).toBeNull();
  });
});
