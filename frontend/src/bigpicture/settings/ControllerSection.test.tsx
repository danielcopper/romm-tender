import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { createElement } from "react";
import { ControllerSection } from "./ControllerSection";

// Local re-mock: ButtonItem must forward `disabled`, DropdownItem must
// capture rgOptions + onChange so we can drive the dropdown without a real
// Steam UI.
type AnyProps = Record<string, unknown> & { children?: unknown };
interface DropdownOption {
  data: unknown;
  label: string;
}
interface DropdownItemProps {
  label?: string;
  rgOptions?: DropdownOption[];
  selectedOption?: unknown;
  onChange?: (option: DropdownOption) => void;
}
const dropdownCaptured: { items: DropdownItemProps[] } = { items: [] };

vi.mock("@decky/ui", () => ({
  PanelSection: (p: AnyProps) => createElement("section", {}, p.children as never),
  PanelSectionRow: (p: AnyProps) => createElement("div", {}, p.children as never),
  Field: (p: AnyProps & { label?: unknown; description?: unknown }) =>
    createElement(
      "div",
      { "data-testid": "field" },
      createElement("span", { "data-testid": "field-label" }, p.label as never),
      createElement("span", { "data-testid": "field-desc" }, p.description as never),
    ),
  ButtonItem: ({
    children,
    onClick,
    disabled,
  }: AnyProps & {
    onClick?: () => void;
    disabled?: boolean;
  }) => createElement("button", { onClick, disabled }, children as never),
  DropdownItem: (p: DropdownItemProps) => {
    dropdownCaptured.items.push(p);
    return createElement("div", { "data-testid": "dropdown" }, p.label as never);
  },
}));

function defaultProps(overrides: Partial<React.ComponentProps<typeof ControllerSection>> = {}) {
  return {
    steamInputMode: "default",
    steamInputStatus: "",
    applying: false,
    onModeChange: vi.fn(),
    onApplyMode: vi.fn(),
    ...overrides,
  };
}

describe("ControllerSection", () => {
  beforeEach(() => {
    dropdownCaptured.items = [];
    vi.clearAllMocks();
  });

  describe("mode dropdown", () => {
    it("renders the three Steam Input mode options", () => {
      render(<ControllerSection {...defaultProps()} />);
      expect(dropdownCaptured.items).toHaveLength(1);
      const opts = dropdownCaptured.items[0]?.rgOptions ?? [];
      expect(opts.map((o) => o.data)).toEqual(["default", "force_on", "force_off"]);
    });

    it("forwards steamInputMode as selectedOption", () => {
      render(<ControllerSection {...defaultProps({ steamInputMode: "force_on" })} />);
      expect(dropdownCaptured.items[0]?.selectedOption).toBe("force_on");
    });

    it("dispatches onModeChange with option.data on dropdown change", () => {
      const onModeChange = vi.fn();
      render(<ControllerSection {...defaultProps({ onModeChange })} />);
      dropdownCaptured.items[0]?.onChange?.({ data: "force_off", label: "Force Off" });
      expect(onModeChange).toHaveBeenCalledWith("force_off");
    });
  });

  describe("apply button", () => {
    it("fires onApplyMode when clicked", () => {
      const onApplyMode = vi.fn();
      const { getByText } = render(<ControllerSection {...defaultProps({ onApplyMode })} />);
      fireEvent.click(getByText("Apply to All Shortcuts"));
      expect(onApplyMode).toHaveBeenCalledTimes(1);
    });

    it("is disabled and says a run is in flight while applying=true", () => {
      const { getByText, queryByText } = render(<ControllerSection {...defaultProps({ applying: true })} />);
      expect(getByText("Applying to all shortcuts…")).toBeDisabled();
      expect(queryByText("Apply to All Shortcuts")).toBeNull();
    });

    it("is enabled when applying=false", () => {
      const { getByText } = render(<ControllerSection {...defaultProps()} />);
      expect(getByText("Apply to All Shortcuts")).not.toBeDisabled();
    });
  });

  describe("steamInputStatus field", () => {
    it("renders the status Field when non-empty", () => {
      const { getAllByTestId } = render(
        <ControllerSection {...defaultProps({ steamInputStatus: "Applied to 12 shortcuts" })} />,
      );
      const labels = getAllByTestId("field-label").map((el) => el.textContent);
      expect(labels).toContain("Applied to 12 shortcuts");
    });

    it("omits the status Field when empty", () => {
      const { queryAllByTestId } = render(<ControllerSection {...defaultProps()} />);
      expect(queryAllByTestId("field")).toHaveLength(0);
    });
  });

  describe("RetroArch", () => {
    it("offers no input_driver fix: Apply to All Shortcuts is the section's only button", () => {
      const { container } = render(
        <ControllerSection {...defaultProps({ steamInputStatus: "Applied to 12 shortcuts" })} />,
      );
      expect(Array.from(container.querySelectorAll("button"), (b) => b.textContent)).toEqual([
        "Apply to All Shortcuts",
      ]);
      expect(container.textContent).not.toContain("input_driver");
    });
  });
});
