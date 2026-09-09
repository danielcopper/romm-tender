import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { createElement } from "react";
import { RegisteredDevicesSection } from "./RegisteredDevicesSection";
import type { RegisteredDevice } from "../../types";

// Local re-mock. Two things it must NOT drop, because each is what a rule here
// is stated against: `focusable` on a Field and `onActivate` on a Focusable both
// render tabindex="0", which is what makes a row with no control of its own a
// focus stop — and a wide pane scrolls only by moving focus, so a mock that
// dropped them would make an unreachable group look reachable.
type AnyProps = Record<string, unknown> & { children?: unknown };
vi.mock("@decky/ui", () => ({
  PanelSection: (p: AnyProps) => createElement("section", {}, p.children as never),
  PanelSectionRow: (p: AnyProps) => createElement("div", { "data-testid": "row" }, p.children as never),
  Field: (p: AnyProps & { label?: unknown; description?: unknown; focusable?: boolean }) =>
    createElement(
      "div",
      { "data-testid": "field", tabIndex: p.focusable ? 0 : undefined },
      createElement("span", { "data-testid": "field-label" }, p.label as never),
      createElement("span", { "data-testid": "field-desc" }, p.description as never),
    ),
  Focusable: (p: AnyProps & { onActivate?: (e: unknown) => void; style?: unknown }) =>
    createElement(
      "div",
      { "data-testid": "focusable", tabIndex: p.onActivate ? 0 : undefined, style: p.style },
      p.children as never,
    ),
}));

function makeDevice(overrides: Partial<RegisteredDevice> = {}): RegisteredDevice {
  return {
    id: "d1abc12345678",
    name: "Steam Deck",
    platform: "linux",
    client: "Tender",
    client_version: "0.17.1",
    last_seen: "2025-06-15T11:55:00Z",
    created_at: "2025-06-01T10:00:00Z",
    is_current_device: false,
    ...overrides,
  };
}

function defaultProps(overrides: Partial<React.ComponentProps<typeof RegisteredDevicesSection>> = {}) {
  return {
    devicesLoading: false,
    devicesError: null,
    registeredDevices: null,
    ...overrides,
  };
}

/** The three cells of a row, in column order. */
function cells(row: Element): string[] {
  return Array.from(row.children).map((cell) => cell.textContent);
}

describe("RegisteredDevicesSection", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2025-06-15T12:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  describe("loading state", () => {
    it("renders 'Loading...' when devicesLoading is true", () => {
      const { getAllByTestId } = render(<RegisteredDevicesSection {...defaultProps({ devicesLoading: true })} />);
      const labels = getAllByTestId("field-label").map((el) => el.textContent);
      expect(labels).toContain("Loading...");
    });

    it("hides loading even if devicesError is also set (loading takes precedence)", () => {
      const { getAllByTestId } = render(
        <RegisteredDevicesSection {...defaultProps({ devicesLoading: true, devicesError: "boom" })} />,
      );
      const labels = getAllByTestId("field-label").map((el) => el.textContent);
      expect(labels).toContain("Loading...");
      expect(labels).not.toContain("Could not load devices");
    });

    it("draws no column header over a state that has no rows", () => {
      const { queryByTestId } = render(<RegisteredDevicesSection {...defaultProps({ devicesLoading: true })} />);
      expect(queryByTestId("devices-header")).toBeNull();
    });
  });

  describe("error state", () => {
    it("renders 'Could not load devices' with the error in the description", () => {
      const { getAllByTestId } = render(
        <RegisteredDevicesSection {...defaultProps({ devicesError: "Network unreachable" })} />,
      );
      const labels = getAllByTestId("field-label").map((el) => el.textContent);
      const descs = getAllByTestId("field-desc").map((el) => el.textContent);
      expect(labels).toContain("Could not load devices");
      expect(descs).toContain("Network unreachable");
    });

    it("draws no column header, and no row, over an error", () => {
      const { queryByTestId } = render(<RegisteredDevicesSection {...defaultProps({ devicesError: "boom" })} />);
      expect(queryByTestId("devices-header")).toBeNull();
      expect(queryByTestId("device-row")).toBeNull();
    });
  });

  describe("empty state", () => {
    it("renders 'No devices registered' when the list is empty", () => {
      const { getAllByTestId } = render(<RegisteredDevicesSection {...defaultProps({ registeredDevices: [] })} />);
      const labels = getAllByTestId("field-label").map((el) => el.textContent);
      expect(labels).toContain("No devices registered");
    });

    it("draws no column header over an empty list", () => {
      const { queryByTestId } = render(<RegisteredDevicesSection {...defaultProps({ registeredDevices: [] })} />);
      expect(queryByTestId("devices-header")).toBeNull();
    });

    it("renders nothing meaningful when registeredDevices is null and no loading/error", () => {
      const { queryAllByTestId } = render(<RegisteredDevicesSection {...defaultProps()} />);
      expect(queryAllByTestId("field")).toHaveLength(0);
      expect(queryAllByTestId("device-row")).toHaveLength(0);
    });
  });

  describe("the table", () => {
    it("names its three columns, in order", () => {
      const { getByTestId } = render(
        <RegisteredDevicesSection {...defaultProps({ registeredDevices: [makeDevice()] })} />,
      );
      expect(cells(getByTestId("devices-header"))).toEqual(["Device", "Client", "Last seen"]);
    });

    it("does not make the header a focus stop — the column names lead nowhere", () => {
      const { getByTestId } = render(
        <RegisteredDevicesSection {...defaultProps({ registeredDevices: [makeDevice()] })} />,
      );
      // The header is a plain div, and the Focusable rows are its siblings: no
      // ancestor of it carries a tabindex either.
      for (let el: Element | null = getByTestId("devices-header"); el; el = el.parentElement) {
        expect(el.getAttribute("tabindex")).toBeNull();
      }
    });

    it("renders one row per device, each a focus stop so the table can be walked", () => {
      const devices = [
        makeDevice({ id: "device-aaaaaaaa", name: "Steam Deck" }),
        makeDevice({ id: "device-bbbbbbbb", name: "Desktop" }),
      ];
      const { getAllByTestId } = render(<RegisteredDevicesSection {...defaultProps({ registeredDevices: devices })} />);
      const rows = getAllByTestId("device-row");
      expect(rows).toHaveLength(2);
      expect(rows.map((row) => row.parentElement?.getAttribute("tabindex"))).toEqual(["0", "0"]);
    });

    it("puts the name, the client with its version, and the relative time in their own columns", () => {
      const device = makeDevice({
        name: "Steam Deck",
        // What this plugin registers under today is `DISPLAY_NAME`, "Tender"
        // (`domain/identity.py`). The older spelling is used here because the
        // column has to render what the SERVER holds, and the server keeps the
        // rows earlier versions wrote — a real listing shows both.
        client: "decky-romm-sync",
        client_version: "0.17.1",
        last_seen: "2025-06-15T11:55:00Z", // 5 minutes ago
      });
      const { getByTestId } = render(<RegisteredDevicesSection {...defaultProps({ registeredDevices: [device] })} />);
      expect(cells(getByTestId("device-row"))).toEqual(["Steam Deck", "decky-romm-sync v0.17.1", "5m ago"]);
    });

    it("carries neither the platform nor the id — the two facts the columns dropped", () => {
      const device = makeDevice({ platform: "linux", id: "device-aaaaaaaa-rest-of-uuid" });
      const { getByTestId } = render(<RegisteredDevicesSection {...defaultProps({ registeredDevices: [device] })} />);
      const row = getByTestId("device-row");
      expect(row.textContent).not.toContain("linux");
      expect(row.textContent).not.toContain("device-a");
      // Not smuggled back into a tooltip either.
      const titles = Array.from(row.querySelectorAll("[title]")).map((el) => el.getAttribute("title"));
      expect(titles.join(" ")).not.toContain("linux");
      expect(titles.join(" ")).not.toContain("device-a");
    });

    it("clips a long device name and hands the whole of it back in a title", () => {
      const name = "Daniel's living-room machine with a very long name indeed";
      const { getByTestId } = render(
        <RegisteredDevicesSection {...defaultProps({ registeredDevices: [makeDevice({ name })] })} />,
      );
      const nameCell = getByTestId("device-row").children[0] as HTMLElement;
      expect(nameCell.getAttribute("title")).toBe(name);
      // The clip lives on the flex ITEM holding the name, not on the flex
      // container around it — clipping the container would take the
      // "(this device)" marker away with the overflow.
      const nameSpan = nameCell.firstElementChild as HTMLElement;
      expect(nameSpan.style.overflow).toBe("hidden");
      expect(nameSpan.style.textOverflow).toBe("ellipsis");
      expect(nameSpan.style.whiteSpace).toBe("nowrap");
      expect(nameSpan.style.minWidth).toBe("0");
    });

    it("falls back to 'unknown client v?' when client/client_version are null", () => {
      const device = makeDevice({ client: null, client_version: null });
      const { getByTestId } = render(<RegisteredDevicesSection {...defaultProps({ registeredDevices: [device] })} />);
      expect(cells(getByTestId("device-row"))[1]).toBe("unknown client v?");
    });

    it("renders '(unnamed)' when device.name is null", () => {
      const device = makeDevice({ name: null });
      const { getByTestId } = render(<RegisteredDevicesSection {...defaultProps({ registeredDevices: [device] })} />);
      expect(cells(getByTestId("device-row"))[0]).toBe("(unnamed)");
    });

    it("renders 'never' in Last seen when the device has never been seen", () => {
      const device = makeDevice({ last_seen: null });
      const { getByTestId } = render(<RegisteredDevicesSection {...defaultProps({ registeredDevices: [device] })} />);
      expect(cells(getByTestId("device-row"))[2]).toBe("never");
    });

    it("marks the current device, and only it", () => {
      const devices = [
        makeDevice({ id: "device-aaaa1111", name: "Steam Deck", is_current_device: true }),
        makeDevice({ id: "device-bbbb2222", name: "Desktop", is_current_device: false }),
      ];
      const { getAllByTestId, container } = render(
        <RegisteredDevicesSection {...defaultProps({ registeredDevices: devices })} />,
      );
      const matches = container.textContent.match(/\(this device\)/g) ?? [];
      expect(matches).toHaveLength(1);
      // On the current device's row, in its Device cell.
      expect(cells(getAllByTestId("device-row")[0]!)[0]).toBe("Steam Deck(this device)");
    });
  });
});
