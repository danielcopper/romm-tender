import { globSync, readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { THROBBER_STYLE_ID } from "../utils/throbberStyle";
import { Throbber, ThrobberLabel } from "./Throbber";

afterEach(() => document.getElementById(THROBBER_STYLE_ID)?.remove());

describe("Throbber", () => {
  it("draws Tender's throbber, hidden from assistive technology", () => {
    const { getByTestId } = render(<Throbber />);
    const throbber = getByTestId("spinner");
    expect(throbber.classList.contains("romm-throbber")).toBe(true);
    expect(throbber.getAttribute("aria-hidden")).toBe("true");
    expect(throbber.style.width).toBe("1em");
    expect(throbber.style.height).toBe("1em");
  });

  it("takes the size a place asks for", () => {
    const { getByTestId } = render(<Throbber size="24px" />);
    expect(getByTestId("spinner").style.width).toBe("24px");
  });

  it("brings its stylesheet into the document it is drawn in, not the one the code runs in", () => {
    // The panel's code runs in one document while the Quick Access view, the
    // game page and a modal each draw into their own.
    const other = document.implementation.createHTMLDocument("quick access");
    const container = other.createElement("div");
    other.body.appendChild(container);
    render(<Throbber />, { container });
    expect(other.getElementById(THROBBER_STYLE_ID)?.textContent).toContain(".romm-throbber");
    expect(document.getElementById(THROBBER_STYLE_ID)).toBeNull();
  });
});

describe("ThrobberLabel", () => {
  it("puts the throbber left of the label while busy, and the label stays the button's text", () => {
    const { getByRole } = render(
      <button type="button">
        <ThrobberLabel busy>Syncing...</ThrobberLabel>
      </button>,
    );
    const button = getByRole("button", { name: "Syncing..." });
    expect(button.textContent).toBe("Syncing...");
    expect(button.firstElementChild?.getAttribute("data-testid")).toBe("spinner");
    expect(button.lastChild?.nodeType).toBe(Node.TEXT_NODE);
  });

  it("shows the label alone while not busy", () => {
    const { getByRole, queryByTestId } = render(
      <button type="button">
        <ThrobberLabel busy={false}>Sync All Saves Now</ThrobberLabel>
      </button>,
    );
    expect(getByRole("button", { name: "Sync All Saves Now" }).textContent).toBe("Sync All Saves Now");
    expect(queryByTestId("spinner")).toBeNull();
  });
});

describe("the panel's one spinner", () => {
  it("is the throbber: no shipped module imports @decky/ui's Spinner", () => {
    const srcDir = `${process.cwd()}/src/`;
    const shipped = globSync("**/*.{ts,tsx}", { cwd: srcDir }).filter(
      (file) => !/\.(test|spec)\.tsx?$/.test(file) && !file.startsWith("test-utils/") && file !== "test-setup.ts",
    );
    expect(shipped.length).toBeGreaterThan(100);
    const importing = shipped.filter((file) =>
      [...readFileSync(`${srcDir}${file}`, "utf8").matchAll(/import\s+\{([^}]*)\}\s+from\s+"@decky\/ui"/g)].some(
        (match) => (match[1] ?? "").split(",").some((member) => member.trim().split(" as ")[0]?.trim() === "Spinner"),
      ),
    );
    expect(importing).toEqual([]);
  });
});
