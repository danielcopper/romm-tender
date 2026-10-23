// How a focused BIOS row stop looks is the injected stylesheet's, and why is the
// rule's own comment (`utils/styleInjector.ts`). The sheet lands in Steam's
// document, which happy-dom has not got, so what is pinned is the rule's text —
// and that its values are FOCUSED_ROW's, which it copies because `utils/` may
// not import from a surface.

import { describe, it, expect, vi, afterEach } from "vitest";
import { FOCUSED_ROW } from "../layout/pane";

const spWindow = { window: { document } } as unknown as Window;
vi.mock("../../utils/deckyUiInternals", () => ({ findSP: vi.fn(() => spWindow) }));

import { hideNativePlaySection, showNativePlaySection } from "../../utils/styleInjector";

function rowStopFocusRule(): string {
  const css = [...document.head.querySelectorAll("style")].map((el) => el.textContent).join("\n");
  return /\.romm-bios-row-stop\.gpfocus\s*\{([^}]*)\}/.exec(css)?.[1] ?? "";
}

describe("a focused BIOS row stop", () => {
  afterEach(() => showNativePlaySection());

  it("keeps the row's own text colour and draws a focused table row's fill and marker", () => {
    hideNativePlaySection("native-play");
    const rule = rowStopFocusRule();
    expect(rule).toMatch(/color:\s*inherit !important;/);
    expect(rule).toContain(`background: ${FOCUSED_ROW.background} !important;`);
    expect(rule).toContain(`box-shadow: ${FOCUSED_ROW.boxShadow} !important;`);
  });
});
