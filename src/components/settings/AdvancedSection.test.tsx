import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { createElement } from "react";
import { AdvancedSection } from "./AdvancedSection";

// DropdownItem isn't in the global @decky/ui stub. Capture rgOptions +
// selectedOption + onChange so we can drive the onChange callback and assert
// the wiring without rendering a real Steam Dropdown. ToggleField is captured
// the same way, for the same reason: the local factory replaces the global stub
// for this file, so every name this component imports has to be defined here.
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
interface ToggleFieldProps {
  label?: string;
  description?: string;
  checked?: boolean;
  onChange?: (value: boolean) => void;
}
const captured: { items: DropdownItemProps[]; toggles: ToggleFieldProps[] } = { items: [], toggles: [] };

vi.mock("@decky/ui", () => {
  type AnyProps = Record<string, unknown> & { children?: unknown };
  const passthrough = (tag: string) => (p: AnyProps) => createElement(tag, {}, p.children as never);
  return {
    PanelSection: passthrough("section"),
    PanelSectionRow: passthrough("div"),
    DropdownItem: (p: DropdownItemProps) => {
      captured.items.push(p);
      return createElement("div", { "data-testid": "dropdown" }, p.label as never);
    },
    ToggleField: (p: ToggleFieldProps) => {
      captured.toggles.push(p);
      return createElement("div", { "data-testid": "toggle" }, p.label as never);
    },
  };
});

const renderSection = (props: Partial<Parameters<typeof AdvancedSection>[0]> = {}) =>
  render(
    <AdvancedSection
      logLevel="info"
      onLogLevelChange={vi.fn()}
      updateCheckEnabled={true}
      onUpdateCheckEnabledChange={vi.fn()}
      {...props}
    />,
  );

describe("AdvancedSection", () => {
  beforeEach(() => {
    captured.items = [];
    captured.toggles = [];
  });

  it("renders the log-level dropdown with the four canonical options", () => {
    renderSection({ logLevel: "info" });
    expect(captured.items).toHaveLength(1);
    const item = captured.items[0];
    expect(item?.label).toBe("Log Level");
    expect(item?.rgOptions?.map((o) => o.data)).toEqual(["error", "warn", "info", "debug"]);
  });

  it("forwards the current logLevel as selectedOption", () => {
    renderSection({ logLevel: "debug" });
    expect(captured.items[0]?.selectedOption).toBe("debug");
  });

  it("dispatches onLogLevelChange with option.data when the dropdown fires", () => {
    const onChange = vi.fn();
    renderSection({ onLogLevelChange: onChange });
    captured.items[0]?.onChange?.({ data: "warn", label: "Warn" });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("warn");
  });

  it("passes string values straight through (no transformation)", () => {
    const onChange = vi.fn();
    renderSection({ logLevel: "error", onLogLevelChange: onChange });
    captured.items[0]?.onChange?.({ data: "error", label: "Error" });
    expect(onChange).toHaveBeenCalledWith("error");
  });

  it("renders the update-check toggle at the caller's position", () => {
    renderSection({ updateCheckEnabled: false });
    expect(captured.toggles).toHaveLength(1);
    expect(captured.toggles[0]?.label).toBe("Check for plugin updates");
    expect(captured.toggles[0]?.checked).toBe(false);
  });

  it("names GitHub in the update-check description, since it is the one non-RomM destination", () => {
    renderSection();
    expect(captured.toggles[0]?.checked).toBe(true);
    expect(captured.toggles[0]?.description).toContain("GitHub");
  });

  it("dispatches onUpdateCheckEnabledChange with the new position", () => {
    const onChange = vi.fn();
    renderSection({ updateCheckEnabled: true, onUpdateCheckEnabledChange: onChange });
    captured.toggles[0]?.onChange?.(false);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(false);
  });
});
