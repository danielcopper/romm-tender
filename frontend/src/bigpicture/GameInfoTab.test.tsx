import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { GameInfoTab } from "./GameInfoTab";

const PROPS = {
  romName: "Mario Golf",
  regions: [],
  languages: [],
  metadata: null,
  platformName: "N64",
  installed: false,
  installedRom: null,
};

describe("GameInfoTab's cover (#2381 D18)", () => {
  beforeEach(() => {
    vi.stubGlobal("appStore", {
      GetAppOverviewByAppID: vi.fn(() => ({ appid: 5000 })),
      GetCustomVerticalCapsuleURLs: vi.fn(() => ["/customimages/5000p.jpg?v=9", "/customimages/5000p.png?v=9"]),
      allApps: [],
    });
  });

  it("shows the cover Steam shows, first of Steam's URLs first", () => {
    const { container } = render(<GameInfoTab {...PROPS} appId={5000} coverBase64="ROMM" />);

    expect(container.querySelector("img")?.getAttribute("src")).toBe(
      "https://steamloopback.host/customimages/5000p.jpg?v=9",
    );
  });

  it("falls back to RomM's cover where none of Steam's loads", () => {
    const { container } = render(<GameInfoTab {...PROPS} appId={5000} coverBase64="ROMM" />);

    fireEvent.error(container.querySelector("img")!);
    fireEvent.error(container.querySelector("img")!);

    expect(container.querySelector("img")?.getAttribute("src")).toBe("data:image/png;base64,ROMM");
  });

  it("shows RomM's cover where Steam names none", () => {
    vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(() => null), allApps: [] });

    const { container } = render(<GameInfoTab {...PROPS} appId={5000} coverBase64="ROMM" />);

    expect(container.querySelector("img")?.getAttribute("src")).toBe("data:image/png;base64,ROMM");
  });
});
