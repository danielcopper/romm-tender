// The Library page's Collections tab, driven through the page it lives on: the
// kinds in the list column, the selected kind's pane, the read behind them and
// the four writes. `CollectionsTab`, `CollectionsDetail` and
// `useCollectionsPage` only exist together, so they are exercised as one here;
// the rules they compute from are pinned on their own in
// `collectionKinds.test.ts`.
//
// CATCH-REJECTION ASSERTION RULE: every write's refusal and rejection is
// asserted through what the reader then sees — the control back where it was,
// and the line that says why, in the column the write was made in.
//
// Focus selects, so a kind is selected by firing focusin on its row, which is
// what Steam's navigation does; the @decky/ui stub in `frontend/src/test-setup.ts`
// forwards `onFocus` on a Focusable for exactly that reason.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import type { ReactElement } from "react";
import { showModal } from "@decky/ui";
import { LibraryPage } from "../LibraryPage";
import * as backend from "../../api/backend";
import type { CollectionSyncSetting, PluginSettings } from "../../types";
import { COLLECTION_RENDER_CAP } from "./CollectionsDetail";
import { CONFIRM_ABOVE } from "./collectionKinds";
import { ENTRY_FOCUS_DELAY_MS, ENTRY_STOP_ATTR } from "../../utils/entryFocus";

vi.mock("../../utils/deckyUiInternals", async () => {
  const { createElement: ce } = await import("react");
  type TabShape = { id: string; title: string; content: unknown };
  return {
    quickAccessMenuClasses: undefined,
    ScrollPanel: undefined,
    findSP: () => undefined,
    ControllerGlyph: undefined,
    GLYPH_BUTTON_B: 1,
    Tabs: ({ tabs, activeTab, onShowTab }: { tabs: TabShape[]; activeTab: string; onShowTab: (id: string) => void }) =>
      ce(
        "div",
        { "data-testid": "steam-tabs" },
        ...tabs.map((tab) =>
          ce("button", { key: tab.id, "data-testid": `tab-${tab.id}`, onClick: () => onShowTab(tab.id) }, tab.title),
        ),
        ce("div", { key: "content" }, tabs.find((tab) => tab.id === activeTab)?.content as never),
      ),
  };
});

function settings(overrides: Partial<PluginSettings> = {}): PluginSettings {
  return {
    romm_url: "",
    has_token: true,
    steam_input_mode: "default",
    sgdb_api_key_masked: "",
    log_level: "warn",
    romm_allow_insecure_ssl: false,
    ...overrides,
  };
}

function coll(overrides: Partial<CollectionSyncSetting> = {}): CollectionSyncSetting {
  return {
    id: "1",
    name: "Couch co-op",
    rom_count: 10,
    sync_enabled: false,
    kind: "standard",
    is_favorite: false,
    is_own: true,
    ...overrides,
  };
}

const FAVORITES = coll({ id: "fav", name: "Favourites", is_favorite: true, rom_count: 14, sync_enabled: true });
const MINE_ON = coll({ id: "10", name: "Kids", sync_enabled: true, in_steam_count: 17, rom_count: 17 });
const MINE_OFF = coll({ id: "11", name: "Finished", in_steam_count: 41, rom_count: 58 });
const THEIRS = coll({ id: "12", name: "Handheld picks", is_own: false, owner_username: "jonas", rom_count: 22 });
const SMART = coll({ id: "10", name: "Unplayed RPGs", kind: "smart", rom_count: 31 });
const FRANCHISE = coll({ id: "ff", name: "Final Fantasy", kind: "virtual", virtual_type: "franchise", rom_count: 23 });
const IGDB = coll({ id: "zz", name: "Zelda series", kind: "virtual", virtual_type: "collection", rom_count: 12 });

const LIBRARY = [FAVORITES, MINE_ON, MINE_OFF, THEIRS, SMART, FRANCHISE, IGDB];

const settle = () =>
  act(async () => {
    for (let i = 0; i < 8; i++) await Promise.resolve();
  });

async function showTab(container: HTMLElement, id: "collections" | "platforms") {
  fireEvent.click(container.querySelector(`[data-testid="tab-${id}"]`) as HTMLElement);
  await settle();
}

async function openCollections(collections: CollectionSyncSetting[] = LIBRARY) {
  vi.mocked(backend.getCollections).mockResolvedValue({ success: true, collections });
  const view = render(<LibraryPage onBack={vi.fn()} />);
  await settle();
  await showTab(view.container, "collections");
  return view;
}

/** The list row a kind renders — the Focusable ListDetail wraps it in. */
function kindRow(container: HTMLElement, kind: string): HTMLElement {
  const inner = container.querySelector(`[data-testid="kind-row-${kind}"]`);
  if (!inner) throw new Error(`no row for ${kind}`);
  return inner.closest('[data-testid="focusable"]') as HTMLElement;
}

async function selectKind(container: HTMLElement, kind: string) {
  fireEvent.focusIn(kindRow(container, kind));
  await settle();
}

function tableNames(container: HTMLElement): string[] {
  return [...container.querySelectorAll('[data-testid="collection-row"]')].map(
    (row) => row.querySelector("span[title]")?.textContent ?? "",
  );
}

/** A table row's cells in column order — the grid inside PaneTableRow. */
function cellTexts(row: HTMLElement): string[] {
  return [...(row.querySelector("div")?.children ?? [])].map((cell) => cell.textContent);
}

function tableRow(container: HTMLElement, name: string): HTMLElement {
  const row = [...container.querySelectorAll<HTMLElement>('[data-testid="collection-row"]')].find(
    (r) => r.querySelector("span[title]")?.textContent === name,
  );
  if (!row) throw new Error(`no table row for ${name}`);
  return row;
}

const checkbox = (el: HTMLElement) =>
  el.querySelector<HTMLInputElement>('[data-testid="toggle-input"]') as HTMLInputElement;

function button(container: HTMLElement, text: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((b) => b.textContent === text);
  if (!found) throw new Error(`no button ${text}`);
  return found;
}

async function click(el: HTMLElement) {
  fireEvent.click(el);
  await settle();
}

function typeSearch(container: HTMLElement, value: string) {
  fireEvent.change(container.querySelector('[data-testid="text-field"]') as HTMLInputElement, {
    target: { value },
  });
}

function lastModal(): Record<string, unknown> | null {
  const calls = vi.mocked(showModal).mock.calls;
  const el = calls[calls.length - 1]?.[0] as ReactElement<Record<string, unknown>> | undefined;
  return el?.props ?? null;
}

type WriteAnswer = { success: boolean; message?: string };

/** A write held open until the test answers it. */
function held(): { promise: Promise<WriteAnswer>; answer: (value: WriteAnswer) => Promise<void> } {
  let resolve: (value: WriteAnswer) => void = () => {};
  const promise = new Promise<WriteAnswer>((r) => {
    resolve = r;
  });
  return {
    promise,
    answer: async (value: WriteAnswer) => {
      await act(async () => {
        resolve(value);
        for (let i = 0; i < 6; i++) await Promise.resolve();
      });
    },
  };
}

const listStatus = (c: HTMLElement) => c.querySelector('[data-testid="collections-list-status"]')?.textContent ?? null;
const paneStatus = (c: HTMLElement) => c.querySelector('[data-testid="collections-pane-status"]')?.textContent ?? null;
const ownerSwitch = (c: HTMLElement) =>
  checkbox(
    [...c.querySelectorAll<HTMLElement>('[data-testid="toggle"]')].find((t) =>
      t.textContent.includes("Other users' collections"),
    ) as HTMLElement,
  );

describe("Library › Collections", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(backend.getPlatforms).mockResolvedValue({ success: true, platforms: [] });
    vi.mocked(backend.getFirmwareStatus).mockResolvedValue({ success: true, platforms: [] });
    vi.mocked(backend.getRegistryPlatforms).mockResolvedValue({ platforms: [] });
    vi.mocked(backend.getSettings).mockResolvedValue(settings());
    vi.mocked(backend.saveCollectionSync).mockResolvedValue({ success: true });
    vi.mocked(backend.saveCollectionsSync).mockResolvedValue({ success: true });
    vi.mocked(backend.setCollectionOwnerScope).mockResolvedValue({ success: true });
  });

  describe("the collections read", () => {
    it("states the backend's message when the read is refused, and asks again when the tab is entered again", async () => {
      vi.mocked(backend.getCollections)
        .mockResolvedValueOnce({ success: false, collections: [], message: "Cannot reach the RomM server" })
        .mockResolvedValueOnce({ success: true, collections: LIBRARY });
      const { container } = render(<LibraryPage onBack={vi.fn()} />);
      await settle();
      await showTab(container, "collections");

      const failed = container.querySelector('[data-testid="collections-load-failed"]');
      expect(failed?.textContent).toContain("Cannot reach the RomM server");
      expect(failed?.textContent).toContain("come back to ask again");
      expect(kindRow(container, "standard").textContent).toContain("—");

      await showTab(container, "platforms");
      await showTab(container, "collections");

      expect(vi.mocked(backend.getCollections)).toHaveBeenCalledTimes(2);
      expect(container.querySelector('[data-testid="collections-load-failed"]')).toBeNull();
      expect(tableNames(container)).toEqual(["Kids", "Finished", "Handheld picks"]);
    });

    it("falls back to a sentence of its own when the read is rejected with nothing to quote", async () => {
      vi.mocked(backend.getCollections).mockRejectedValueOnce(new Error("socket closed"));
      const { container } = render(<LibraryPage onBack={vi.fn()} />);
      await settle();
      await showTab(container, "collections");

      expect(container.querySelector('[data-testid="collections-load-failed"]')?.textContent).toContain(
        "Could not read your collections from RomM.",
      );
    });

    it("does not ask twice when the tab is entered again while the read is still out", async () => {
      vi.mocked(backend.getCollections).mockReturnValue(new Promise(() => {}));
      const { container } = render(<LibraryPage onBack={vi.fn()} />);
      await settle();
      await showTab(container, "collections");
      await showTab(container, "platforms");
      await showTab(container, "collections");
      expect(vi.mocked(backend.getCollections)).toHaveBeenCalledTimes(1);
    });

    it("asks for the owner switch's setting again after a failed settings read", async () => {
      vi.mocked(backend.getSettings)
        .mockRejectedValueOnce(new Error("boom"))
        .mockResolvedValueOnce(settings({ collection_owner_scope: "own" }));
      const { container } = await openCollections();
      expect(ownerSwitch(container).checked).toBe(true);

      await showTab(container, "platforms");
      await showTab(container, "collections");

      expect(vi.mocked(backend.getSettings)).toHaveBeenCalledTimes(2);
      expect(ownerSwitch(container).checked).toBe(false);
    });
  });

  describe("the list of kinds", () => {
    it("lists the kinds, then a rule, Favorites and the owner switch's row last, each with what it has on", async () => {
      const { container } = await openCollections();
      const heading = [...container.querySelectorAll("div")].find((d) => d.textContent === "AUTOGENERATED");
      const ownerToggle = [...container.querySelectorAll<HTMLElement>('[data-testid="toggle"]')].find((t) =>
        t.textContent.includes("Other users' collections"),
      );
      const inOrder = [
        container.querySelector('[data-testid="kind-row-standard"]'),
        container.querySelector('[data-testid="kind-row-smart"]'),
        heading,
        container.querySelector('[data-testid="kind-row-franchise"]'),
        container.querySelector('[data-testid="kind-row-igdb"]'),
        container.querySelector('[data-testid="favorites-rule"]'),
        container.querySelector('[data-testid="kind-row-favorites"]'),
        ownerToggle,
        container.querySelector('[data-testid="collections-sentence"]'),
      ];
      expect(inOrder.every((el) => el != null)).toBe(true);
      for (let k = 1; k < inOrder.length; k++) {
        const follows = inOrder[k - 1]!.compareDocumentPosition(inOrder[k]!) & Node.DOCUMENT_POSITION_FOLLOWING;
        expect(follows).toBeTruthy();
      }

      expect(kindRow(container, "standard").textContent).toContain("1 of 3 on");
      expect(kindRow(container, "smart").textContent).toContain("0 of 1 on");
      expect(kindRow(container, "franchise").textContent).toContain("0 of 1 on");
      expect(kindRow(container, "igdb").textContent).toContain("0 of 1 on");
      expect(kindRow(container, "favorites").textContent).toContain("14 ROMs");
    });

    it("leaves a hidden foreign collection stored as on out of its kind's count", async () => {
      vi.mocked(backend.getSettings).mockResolvedValue(settings({ collection_owner_scope: "own" }));
      const { container } = await openCollections([MINE_ON, MINE_OFF, { ...THEIRS, sync_enabled: true }]);
      expect(kindRow(container, "standard").textContent).toContain("1 of 2 on");
    });

    it("makes the four kind rows focus stops that A selects, and leaves A to the Favorites switch", async () => {
      const { container } = await openCollections();
      for (const kind of ["standard", "smart", "franchise", "igdb"]) {
        expect(kindRow(container, kind).dataset.activate).toBe("true");
      }
      expect(kindRow(container, "favorites").dataset.activate).toBeUndefined();

      fireEvent(
        kindRow(container, "smart"),
        new CustomEvent("decky-button-down", { detail: { button: 1 }, bubbles: true }),
      );
      await settle();
      expect(tableNames(container)).toEqual(["Unplayed RPGs"]);
    });

    it("opens on Collections, whose row carries the entry-stop mark, and moves the mark with the selection", async () => {
      const { container } = await openCollections();
      const marked = () => [...container.querySelectorAll(`[${ENTRY_STOP_ATTR}]`)];
      expect(marked()).toHaveLength(1);
      expect(marked()[0]?.querySelector('[data-testid="kind-row-standard"]')).not.toBeNull();

      await selectKind(container, "franchise");
      expect(marked()[0]?.querySelector('[data-testid="kind-row-franchise"]')).not.toBeNull();
    });
  });

  describe("Favorites", () => {
    it("is the signed-in user's own favorites collection, switched from its row", async () => {
      const { container } = await openCollections();
      const row = kindRow(container, "favorites");
      expect(checkbox(row).checked).toBe(true);

      await click(checkbox(row));

      expect(vi.mocked(backend.saveCollectionSync)).toHaveBeenCalledWith("fav", "standard", false);
      expect(tableNames(container)).not.toContain("Favourites");
    });

    it("lists another user's public favorites collection under Collections with its owner, and follows the owner switch", async () => {
      const theirs = coll({
        id: "tf",
        name: "Mara's favourites",
        is_favorite: true,
        is_own: false,
        owner_username: "mara",
      });
      const { container } = await openCollections([FAVORITES, MINE_ON, theirs]);
      expect(tableNames(container)).toEqual(["Kids", "Mara's favourites"]);
      expect(cellTexts(tableRow(container, "Mara's favourites"))[1]).toBe("mara");
      expect(kindRow(container, "favorites").textContent).toContain("14 ROMs");

      await click(ownerSwitch(container));
      expect(tableNames(container)).toEqual(["Kids"]);
    });

    it("stays greyed, says more than one, and lists them under Collections where two count as yours", async () => {
      const second = coll({ id: "fav2", name: "Starred", is_favorite: true });
      const { container } = await openCollections([FAVORITES, second, MINE_ON]);
      const row = kindRow(container, "favorites");

      expect(row.textContent).toContain("more than one, listed under Collections");
      expect(row.querySelector('[data-testid="toggle"]')?.getAttribute("data-disabled")).toBe("true");
      expect(row.dataset.activate).toBe("true");
      expect(tableNames(container)).toEqual(["Favourites", "Kids", "Starred"]);

      await selectKind(container, "favorites");
      expect(container.textContent).toContain("More than one favorites collection counts as yours");
    });

    it("stays greyed with a dash where the account has none", async () => {
      const { container } = await openCollections([MINE_ON]);
      const row = kindRow(container, "favorites");
      expect(row.querySelector('[data-testid="toggle"]')?.getAttribute("data-disabled")).toBe("true");
      expect(row.textContent).toContain("—");
      await selectKind(container, "favorites");
      expect(container.textContent).toContain("Your RomM account has no favorites collection.");
      expect(container.querySelector('[data-testid="collections-sentence"]')).toBeNull();
    });

    it("shows the sentence and the game count on its pane, with no table", async () => {
      const { container } = await openCollections();
      await selectKind(container, "favorites");
      expect(container.querySelector('[data-testid="collections-sentence"]')?.textContent).toContain(
        "Turning it on adds all its games to Steam at the next sync",
      );
      expect(container.textContent).toContain("14 ROMs in it");
      expect(container.querySelectorAll('[data-testid="collection-row"]')).toHaveLength(0);
    });
  });

  describe("a kind's pane", () => {
    it("draws Collections as Collection, Owner, ROMs, In Steam and Sync, on above off", async () => {
      const { container } = await openCollections();
      expect(container.textContent).toContain("Collection");
      expect(container.textContent).toContain("Owner");
      expect(tableNames(container)).toEqual(["Kids", "Finished", "Handheld picks"]);

      expect(cellTexts(tableRow(container, "Kids")).slice(0, 4)).toEqual(["Kids", "you", "17", "17"]);
      // In Steam absent is unknown, never zero.
      expect(cellTexts(tableRow(container, "Handheld picks")).slice(0, 4)).toEqual([
        "Handheld picks",
        "jonas",
        "22",
        "—",
      ]);
      // The Sync cell carries the row's one control, so the row is no stop of its own.
      expect(tableRow(container, "Kids").dataset.activate).toBeUndefined();
      expect(checkbox(tableRow(container, "Kids")).checked).toBe(true);
    });

    it("has no Owner column on Franchises, and lists that type alone", async () => {
      const { container } = await openCollections();
      await selectKind(container, "franchise");
      expect(tableNames(container)).toEqual(["Final Fantasy"]);
      expect(container.textContent).not.toContain("Owner");
      expect(cellTexts(tableRow(container, "Final Fantasy")).slice(0, 3)).toEqual(["Final Fantasy", "23", "—"]);
      expect(container.querySelector('[data-testid="collections-sentence"]')?.textContent).toContain(
        "every game of that franchise",
      );
    });

    it("gives the search field the rest of its line, and sets the table in the compact register", async () => {
      // What happy-dom can see of the layout: the styles that decide it. Whether
      // Steam then honours them is the device's to show.
      const { container } = await openCollections();
      const search = container.querySelector<HTMLElement>('[data-testid="collections-search"]');
      expect(search?.style.flex).toBe("1 1 0px");
      expect(search?.style.minWidth).toBe("0");
      const row = tableRow(container, "Kids");
      expect(row.style.fontSize).toBe("12px");
      expect(row.style.padding).toBe("2px 16px");
    });

    it("fills the whole row and marks its left edge while its toggle holds focus, and drops both on blur", async () => {
      const { container } = await openCollections();
      const row = tableRow(container, "Kids");
      expect(row.style.background).toBe("transparent");

      fireEvent.focusIn(checkbox(row));
      expect(row.style.background).toBe("#3d4450");
      expect(row.style.boxShadow).toBe("inset 3px 0 0 #1a9fff");
      expect(tableRow(container, "Finished").style.background).toBe("transparent");

      fireEvent.focusOut(checkbox(row));
      expect(row.style.background).toBe("transparent");
      expect(row.style.boxShadow).toBe("none");
    });

    it("keeps the Sync cell's own Field from painting a focus fill of its own", async () => {
      const { container } = await openCollections();
      const toggle = tableRow(container, "Kids").querySelector('[data-testid="toggle"]');
      expect(toggle?.getAttribute("data-no-focus-highlight")).toBe("true");
    });

    it("keeps the order it opened with while rows are switched", async () => {
      const { container } = await openCollections();
      await click(checkbox(tableRow(container, "Handheld picks")));
      expect(tableNames(container)).toEqual(["Kids", "Finished", "Handheld picks"]);
    });

    it("narrows by a loose search, and enters another kind without it", async () => {
      const { container } = await openCollections();
      typeSearch(container, "fnsh");
      expect(tableNames(container)).toEqual(["Finished"]);

      await selectKind(container, "smart");
      expect((container.querySelector('[data-testid="text-field"]') as HTMLInputElement).value).toBe("");
      expect(tableNames(container)).toEqual(["Unplayed RPGs"]);
    });

    it(`paints at most ${COLLECTION_RENDER_CAP} rows and says how many more there are`, async () => {
      const many = Array.from({ length: COLLECTION_RENDER_CAP + 7 }, (_, i) =>
        coll({ id: `v${i}`, name: `Franchise ${i}`, kind: "virtual", virtual_type: "franchise" }),
      );
      const { container } = await openCollections(many);
      await selectKind(container, "franchise");
      expect(container.querySelectorAll('[data-testid="collection-row"]')).toHaveLength(COLLECTION_RENDER_CAP);
      expect(container.textContent).toContain("7 more");
    });

    it("says how many of another user's collections the owner switch is hiding from an empty kind", async () => {
      vi.mocked(backend.getSettings).mockResolvedValue(settings({ collection_owner_scope: "own" }));
      const { container } = await openCollections([THEIRS]);
      expect(container.textContent).toContain("Nothing to list under Collections.");
      expect(container.textContent).toContain("1 from other users is hidden");
    });
  });

  describe("switching one collection", () => {
    it("flips at once and writes that one collection", async () => {
      const { container } = await openCollections();
      await click(checkbox(tableRow(container, "Finished")));
      expect(checkbox(tableRow(container, "Finished")).checked).toBe(true);
      expect(vi.mocked(backend.saveCollectionSync)).toHaveBeenCalledWith("11", "standard", true);
    });

    it("puts a refused row back and says why in the pane, not the list column", async () => {
      vi.mocked(backend.saveCollectionSync).mockResolvedValueOnce({ success: false, message: "Migration pending" });
      const { container } = await openCollections();
      await click(checkbox(tableRow(container, "Finished")));

      expect(checkbox(tableRow(container, "Finished")).checked).toBe(false);
      expect(paneStatus(container)).toBe("Migration pending");
      expect(listStatus(container)).toBeNull();
    });

    it("puts a rejected row back and says the write was undone", async () => {
      vi.mocked(backend.saveCollectionSync).mockRejectedValueOnce(new Error("gone"));
      const { container } = await openCollections();
      await click(checkbox(tableRow(container, "Kids")));
      expect(checkbox(tableRow(container, "Kids")).checked).toBe(true);
      expect(paneStatus(container)).toBe("Could not save that; the change was undone.");
    });

    it("takes the pane's line back at the next pane write that succeeds, at another kind, and on leaving the tab", async () => {
      vi.mocked(backend.saveCollectionSync).mockResolvedValue({ success: false, message: "No" });
      const { container } = await openCollections();
      const fail = async () => click(checkbox(tableRow(container, "Finished")));

      await fail();
      expect(paneStatus(container)).toBe("No");
      vi.mocked(backend.saveCollectionSync).mockResolvedValueOnce({ success: true });
      await click(checkbox(tableRow(container, "Kids")));
      expect(paneStatus(container)).toBeNull();

      await fail();
      await selectKind(container, "smart");
      expect(paneStatus(container)).toBeNull();

      await selectKind(container, "standard");
      await fail();
      await showTab(container, "platforms");
      await showTab(container, "collections");
      expect(paneStatus(container)).toBeNull();
    });

    it("says a refused Favorites switch under the owner switch, not in the pane", async () => {
      vi.mocked(backend.saveCollectionSync).mockResolvedValueOnce({ success: false, message: "Migration pending" });
      const { container } = await openCollections();
      await click(checkbox(kindRow(container, "favorites")));

      expect(checkbox(kindRow(container, "favorites")).checked).toBe(true);
      expect(listStatus(container)).toBe("Migration pending");
      expect(paneStatus(container)).toBeNull();

      // Taken back by the next list-column write that succeeds.
      await click(checkbox(kindRow(container, "favorites")));
      expect(listStatus(container)).toBeNull();
    });
  });

  describe("the owner switch", () => {
    it("starts from the stored setting, and turning it off hides another user's collections and writes the scope", async () => {
      const { container } = await openCollections();
      expect(ownerSwitch(container).checked).toBe(true);

      await click(ownerSwitch(container));

      expect(vi.mocked(backend.setCollectionOwnerScope)).toHaveBeenCalledWith("own");
      expect(tableNames(container)).toEqual(["Kids", "Finished"]);
      expect(kindRow(container, "standard").textContent).toContain("1 of 2 on");
    });

    it("goes back and says why in the list column when the write is refused", async () => {
      vi.mocked(backend.setCollectionOwnerScope).mockResolvedValueOnce({ success: false, message: "Unknown scope" });
      const { container } = await openCollections();
      await click(ownerSwitch(container));

      expect(ownerSwitch(container).checked).toBe(true);
      expect(tableNames(container)).toContain("Handheld picks");
      expect(listStatus(container)).toBe("Unknown scope");
      expect(paneStatus(container)).toBeNull();
    });

    it("goes back when the write is rejected, and the line leaves with the tab", async () => {
      vi.mocked(backend.setCollectionOwnerScope).mockRejectedValueOnce(new Error("gone"));
      const { container } = await openCollections();
      await click(ownerSwitch(container));
      expect(ownerSwitch(container).checked).toBe(true);
      expect(listStatus(container)).toBe("Could not save that; the change was undone.");

      await showTab(container, "platforms");
      await showTab(container, "collections");
      expect(listStatus(container)).toBeNull();
    });
  });

  describe("Enable all and Disable all", () => {
    const franchises = (count: number) =>
      Array.from({ length: count }, (_, i) =>
        coll({ id: `f${i}`, name: `Franchise ${i}`, kind: "virtual", virtual_type: "franchise" }),
      );
    const answerModal = async (which: "onOK" | "onCancel") => {
      await act(async () => {
        (lastModal()?.[which] as () => void)();
        for (let i = 0; i < 6; i++) await Promise.resolve();
      });
    };
    const afterRefocus = () =>
      act(async () => {
        await new Promise((resolve) => setTimeout(resolve, ENTRY_FOCUS_DELAY_MS + 10));
      });

    it(`writes at once, with no dialog, when it would switch ${CONFIRM_ABOVE} or fewer`, async () => {
      const { container } = await openCollections(franchises(CONFIRM_ABOVE));
      await selectKind(container, "franchise");
      await click(button(container, "Enable all"));

      expect(vi.mocked(showModal)).not.toHaveBeenCalled();
      const [ids, kind, enabled] = vi.mocked(backend.saveCollectionsSync).mock.calls[0] ?? [];
      expect(ids).toHaveLength(CONFIRM_ABOVE);
      expect([kind, enabled]).toEqual(["virtual", true]);
    });

    it(`asks first when it would switch more than ${CONFIRM_ABOVE}, and writes only on OK`, async () => {
      const { container } = await openCollections(franchises(CONFIRM_ABOVE + 1));
      await selectKind(container, "franchise");
      await click(button(container, "Disable all"));

      expect(vi.mocked(backend.saveCollectionsSync)).not.toHaveBeenCalled();
      const modal = lastModal();
      expect(modal?.strTitle).toBe(`Disable all ${CONFIRM_ABOVE + 1} in Franchises?`);
      expect(modal?.strOKButtonText).toBe("Disable all");
      expect(String(modal?.strDescription)).toContain(
        `This turns off syncing for all ${CONFIRM_ABOVE + 1} collections`,
      );
      expect(String(modal?.strDescription)).toContain("at the next sync");

      await answerModal("onOK");
      const [ids, kind, enabled] = vi.mocked(backend.saveCollectionsSync).mock.calls[0] ?? [];
      expect(ids).toHaveLength(CONFIRM_ABOVE + 1);
      expect([kind, enabled]).toEqual(["virtual", false]);
    });

    it("asks with a search typed too, and then writes every collection it leaves, past the render cap too", async () => {
      const many = Array.from({ length: COLLECTION_RENDER_CAP + 5 }, (_, i) =>
        coll({ id: `v${i}`, name: `Series ${i}`, kind: "virtual", virtual_type: "collection" }),
      );
      const { container } = await openCollections([
        ...many,
        coll({ id: "x", name: "Other", kind: "virtual", virtual_type: "collection" }),
      ]);
      await selectKind(container, "igdb");
      typeSearch(container, "Series");
      await click(button(container, "Enable all"));

      expect(String(lastModal()?.strDescription)).toContain("Their games come to Steam at the next sync");
      expect(String(lastModal()?.strDescription)).toContain(`past the first ${COLLECTION_RENDER_CAP}`);
      await answerModal("onOK");
      const [ids, kind, enabled] = vi.mocked(backend.saveCollectionsSync).mock.calls[0] ?? [];
      expect(ids).toHaveLength(COLLECTION_RENDER_CAP + 5);
      expect(ids).not.toContain("x");
      expect([kind, enabled]).toEqual(["virtual", true]);
    });

    it("writes nothing when the dialog is cancelled, and puts focus back on the button that opened it", async () => {
      const { container } = await openCollections(franchises(CONFIRM_ABOVE + 1));
      await selectKind(container, "franchise");
      await click(button(container, "Enable all"));

      await answerModal("onCancel");
      await afterRefocus();

      expect(vi.mocked(backend.saveCollectionsSync)).not.toHaveBeenCalled();
      const enableAll = button(container, "Enable all");
      expect(enableAll.ownerDocument.activeElement).toBe(enableAll);
      expect(enableAll.classList.contains("gpfocus")).toBe(true);
    });

    it("puts focus back on the button that opened the dialog after OK as well", async () => {
      const { container } = await openCollections(franchises(CONFIRM_ABOVE + 1));
      await selectKind(container, "franchise");
      await click(button(container, "Disable all"));

      await answerModal("onOK");
      await afterRefocus();

      const disableAll = button(container, "Disable all");
      expect(disableAll.ownerDocument.activeElement).toBe(disableAll);
    });

    it("writes that type alone on an autogenerated kind", async () => {
      const second = coll({ id: "mm", name: "Mega Man", kind: "virtual", virtual_type: "franchise" });
      const { container } = await openCollections([...LIBRARY, second]);
      await selectKind(container, "franchise");
      await click(button(container, "Disable all"));

      // The IGDB collection shares the virtual bucket and is not written.
      expect(vi.mocked(backend.saveCollectionsSync)).toHaveBeenCalledWith(["ff", "mm"], "virtual", false);
    });

    it("writes the table and flips its rows", async () => {
      const { container } = await openCollections();
      await click(button(container, "Enable all"));
      expect(vi.mocked(backend.saveCollectionsSync)).toHaveBeenCalledWith(["10", "11", "12"], "standard", true);
      expect(tableNames(container).map((n) => checkbox(tableRow(container, n)).checked)).toEqual([true, true, true]);
    });

    it("leaves the favorites collection out and, with the owner switch off, every other user's", async () => {
      vi.mocked(backend.getSettings).mockResolvedValue(settings({ collection_owner_scope: "own" }));
      const { container } = await openCollections();
      await click(button(container, "Disable all"));
      expect(vi.mocked(backend.saveCollectionsSync)).toHaveBeenCalledWith(["10", "11"], "standard", false);
    });

    it("puts every row back and says why in the pane when the write is refused", async () => {
      vi.mocked(backend.saveCollectionsSync).mockResolvedValueOnce({ success: false, message: "Migration pending" });
      const { container } = await openCollections();
      typeSearch(container, "i");
      await click(button(container, "Enable all"));

      expect(tableNames(container).map((n) => checkbox(tableRow(container, n)).checked)).toEqual([true, false, false]);
      expect(paneStatus(container)).toBe("Migration pending");
      expect(listStatus(container)).toBeNull();
    });

    it("puts every row back when the write is rejected", async () => {
      vi.mocked(backend.saveCollectionsSync).mockRejectedValueOnce(new Error("gone"));
      const { container } = await openCollections();
      typeSearch(container, "i");
      await click(button(container, "Disable all"));

      expect(checkbox(tableRow(container, "Kids")).checked).toBe(true);
      expect(paneStatus(container)).toBe("Could not save that; the change was undone.");
    });

    it("stays disabled while nothing is listed", async () => {
      const { container } = await openCollections([MINE_ON]);
      typeSearch(container, "qqq");
      expect(button(container, "Enable all").disabled).toBe(true);
      expect(button(container, "Disable all").disabled).toBe(true);
    });

    it("writes the favorites collections too while the Favorites row is greyed as more than one", async () => {
      const second = coll({ id: "fav2", name: "Starred", is_favorite: true });
      const { container } = await openCollections([FAVORITES, second, MINE_ON]);
      await click(button(container, "Enable all"));
      const [ids] = vi.mocked(backend.saveCollectionsSync).mock.calls[0] ?? [];
      expect(ids).toEqual(expect.arrayContaining(["fav", "fav2", "10"]));
      expect(ids).toHaveLength(3);
    });
  });

  describe("the owner switch's row", () => {
    it("is a row whose switch takes A, and selecting it shows its own pane with no table", async () => {
      const { container } = await openCollections();
      const row = kindRow(container, "owner");
      expect(row.dataset.activate).toBeUndefined();
      expect(row.querySelector('[data-testid="toggle-input"]')).not.toBeNull();

      await selectKind(container, "owner");

      expect(container.textContent).toContain(
        "Turned off, other users' collections are hidden here and left out of the sync",
      );
      expect(container.textContent).toContain("1 from other users right now — shown and synced.");
      expect(container.querySelectorAll('[data-testid="collection-row"]')).toHaveLength(0);
      expect(container.querySelector('[data-testid="collections-search"]')).toBeNull();
      // The selection marker, as every other row of the list draws it.
      expect(row.firstElementChild?.getAttribute("style") ?? "").toContain("border-left-color: #1a9fff");
    });

    it("says how many are hidden once the switch is off", async () => {
      const { container } = await openCollections();
      await selectKind(container, "owner");
      await click(ownerSwitch(container));
      expect(container.textContent).toContain("1 from other users right now — hidden and left out of the sync.");
      expect(kindRow(container, "owner").textContent).toContain("1 hidden");
    });

    it("says nothing is hidden while Tender does not know the account", async () => {
      const { container } = await openCollections([coll({ id: "n", name: "Shared", is_own: null })]);
      await selectKind(container, "owner");
      expect(container.textContent).toContain(
        "Tender does not know your RomM account yet, so nothing is hidden for now.",
      );
    });

    it("enters a kind afresh after it: no search carried over, no pane line", async () => {
      vi.mocked(backend.saveCollectionSync).mockResolvedValueOnce({ success: false, message: "No" });
      const { container } = await openCollections();
      typeSearch(container, "fnsh");
      await click(checkbox(tableRow(container, "Finished")));
      await selectKind(container, "owner");
      await selectKind(container, "standard");

      expect((container.querySelector('[data-testid="text-field"]') as HTMLInputElement).value).toBe("");
      expect(paneStatus(container)).toBeNull();
    });
  });

  describe("a refusal that carries no message", () => {
    const FALLBACK = "Could not save that; the change was undone.";

    it("says the collections read failed in a sentence of its own", async () => {
      vi.mocked(backend.getCollections).mockResolvedValueOnce({ success: false, collections: [] });
      const { container } = render(<LibraryPage onBack={vi.fn()} />);
      await settle();
      await showTab(container, "collections");
      expect(container.querySelector('[data-testid="collections-load-failed"]')?.textContent).toContain(
        "Could not read your collections from RomM.",
      );
    });

    it("says the change was undone, for a table switch", async () => {
      vi.mocked(backend.saveCollectionSync).mockResolvedValueOnce({ success: false });
      const { container } = await openCollections();
      await click(checkbox(tableRow(container, "Finished")));
      expect(paneStatus(container)).toBe(FALLBACK);
    });

    it("says the change was undone, for the Favorites switch", async () => {
      vi.mocked(backend.saveCollectionSync).mockResolvedValueOnce({ success: false });
      const { container } = await openCollections();
      await click(checkbox(kindRow(container, "favorites")));
      expect(listStatus(container)).toBe(FALLBACK);
    });

    it("says the change was undone, for the owner switch", async () => {
      vi.mocked(backend.setCollectionOwnerScope).mockResolvedValueOnce({ success: false });
      const { container } = await openCollections();
      await click(ownerSwitch(container));
      expect(listStatus(container)).toBe(FALLBACK);
    });

    it("says the change was undone, for Enable all", async () => {
      vi.mocked(backend.saveCollectionsSync).mockResolvedValueOnce({ success: false });
      const { container } = await openCollections();
      typeSearch(container, "i");
      await click(button(container, "Enable all"));
      expect(paneStatus(container)).toBe(FALLBACK);
    });
  });

  describe("a write that answers late", () => {
    it("says nothing in the list column once a later list write has answered", async () => {
      const favorites = held();
      vi.mocked(backend.saveCollectionSync).mockReturnValueOnce(favorites.promise);
      const { container } = await openCollections();
      await click(checkbox(kindRow(container, "favorites")));
      await click(ownerSwitch(container));

      await favorites.answer({ success: false, message: "Too late" });

      expect(listStatus(container)).toBeNull();
      expect(checkbox(kindRow(container, "favorites")).checked).toBe(true);
    });

    it("does not take back a later pane refusal's line when an earlier write succeeds", async () => {
      const first = held();
      vi.mocked(backend.saveCollectionSync)
        .mockReturnValueOnce(first.promise)
        .mockResolvedValueOnce({ success: false, message: "Refused" });
      const { container } = await openCollections();
      await click(checkbox(tableRow(container, "Kids")));
      await click(checkbox(tableRow(container, "Finished")));
      expect(paneStatus(container)).toBe("Refused");

      await first.answer({ success: true });

      expect(paneStatus(container)).toBe("Refused");
    });

    it("says nothing in the pane once another kind is selected", async () => {
      const write = held();
      vi.mocked(backend.saveCollectionSync).mockReturnValueOnce(write.promise);
      const { container } = await openCollections();
      await click(checkbox(tableRow(container, "Finished")));
      await selectKind(container, "smart");

      await write.answer({ success: false, message: "Too late" });
      expect(paneStatus(container)).toBeNull();

      await selectKind(container, "standard");
      expect(paneStatus(container)).toBeNull();
      expect(checkbox(tableRow(container, "Finished")).checked).toBe(false);
    });

    it("says nothing once the tab has been left and entered again", async () => {
      const write = held();
      vi.mocked(backend.saveCollectionSync).mockReturnValueOnce(write.promise);
      const { container } = await openCollections();
      await click(checkbox(kindRow(container, "favorites")));
      await showTab(container, "platforms");
      await showTab(container, "collections");

      await write.answer({ success: false, message: "Too late" });

      expect(listStatus(container)).toBeNull();
      expect(checkbox(kindRow(container, "favorites")).checked).toBe(true);
    });
  });

  describe("a control whose writes answer late", () => {
    const REFUSED = { success: false, message: "Migration pending" };

    it("shows what is stored after a row is switched twice and both writes are refused", async () => {
      const on = held();
      const off = held();
      vi.mocked(backend.saveCollectionSync).mockReturnValueOnce(on.promise).mockReturnValueOnce(off.promise);
      const { container } = await openCollections();
      await click(checkbox(tableRow(container, "Finished")));
      await click(checkbox(tableRow(container, "Finished")));

      await on.answer(REFUSED);
      await off.answer(REFUSED);

      expect(checkbox(tableRow(container, "Finished")).checked).toBe(false);
    });

    it("keeps a row's newest value when an older write is refused, and shows it once the newest is stored", async () => {
      const first = held();
      const second = held();
      const third = held();
      vi.mocked(backend.saveCollectionSync)
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise)
        .mockReturnValueOnce(third.promise);
      const { container } = await openCollections();
      for (let i = 0; i < 3; i++) await click(checkbox(tableRow(container, "Finished")));

      await first.answer(REFUSED);
      expect(checkbox(tableRow(container, "Finished")).checked).toBe(true);
      await second.answer({ success: true });
      await third.answer({ success: true });

      expect(checkbox(tableRow(container, "Finished")).checked).toBe(true);
    });

    it("shows the stored scope after the owner switch is turned twice and both writes fail", async () => {
      const off = held();
      const on = held();
      vi.mocked(backend.setCollectionOwnerScope).mockReturnValueOnce(off.promise).mockReturnValueOnce(on.promise);
      const { container } = await openCollections();
      await click(ownerSwitch(container));
      await click(ownerSwitch(container));

      await off.answer(REFUSED);
      await on.answer(REFUSED);

      expect(ownerSwitch(container).checked).toBe(true);
    });

    it("keeps the owner switch's newest value when an older write is refused", async () => {
      const first = held();
      const second = held();
      const third = held();
      vi.mocked(backend.setCollectionOwnerScope)
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise)
        .mockReturnValueOnce(third.promise);
      const { container } = await openCollections();
      for (let i = 0; i < 3; i++) await click(ownerSwitch(container));

      await first.answer(REFUSED);
      expect(ownerSwitch(container).checked).toBe(false);
      await second.answer({ success: true });
      await third.answer({ success: true });

      expect(ownerSwitch(container).checked).toBe(false);
    });

    it("leaves a row switched on its own since alone when an earlier Enable all is refused", async () => {
      const batch = held();
      const single = held();
      vi.mocked(backend.saveCollectionsSync).mockReturnValueOnce(batch.promise);
      vi.mocked(backend.saveCollectionSync).mockReturnValueOnce(single.promise);
      const { container } = await openCollections();
      typeSearch(container, "i");
      await click(button(container, "Enable all"));
      // Kids was on before the batch, and is switched off on its own after it.
      await click(checkbox(tableRow(container, "Kids")));

      await batch.answer(REFUSED);
      expect(checkbox(tableRow(container, "Kids")).checked).toBe(false);
      expect(checkbox(tableRow(container, "Finished")).checked).toBe(false);

      await single.answer({ success: true });
      expect(checkbox(tableRow(container, "Kids")).checked).toBe(false);
    });
  });

  describe("the owner switch against its settings read", () => {
    it("keeps a switch flipped while the settings read was out, once the read answers with the old value", async () => {
      let answerRead: (value: PluginSettings) => void = () => {};
      vi.mocked(backend.getSettings).mockReturnValueOnce(
        new Promise<PluginSettings>((r) => {
          answerRead = r;
        }),
      );
      const write = held();
      vi.mocked(backend.setCollectionOwnerScope).mockReturnValueOnce(write.promise);
      const { container } = await openCollections();
      await click(ownerSwitch(container));
      expect(ownerSwitch(container).checked).toBe(false);

      await act(async () => {
        answerRead(settings({ collection_owner_scope: "all" }));
        for (let i = 0; i < 6; i++) await Promise.resolve();
      });
      expect(ownerSwitch(container).checked).toBe(false);

      await write.answer({ success: true });
      expect(ownerSwitch(container).checked).toBe(false);
    });

    it("goes back to the scope a settings read found while the refused write was in flight", async () => {
      let answerRead: (value: PluginSettings) => void = () => {};
      vi.mocked(backend.getSettings).mockReturnValueOnce(
        new Promise<PluginSettings>((r) => {
          answerRead = r;
        }),
      );
      const write = held();
      vi.mocked(backend.setCollectionOwnerScope).mockReturnValueOnce(write.promise);
      const { container } = await openCollections();
      await click(ownerSwitch(container));

      await act(async () => {
        answerRead(settings({ collection_owner_scope: "own" }));
        for (let i = 0; i < 6; i++) await Promise.resolve();
      });
      await write.answer({ success: false, message: "No" });

      expect(ownerSwitch(container).checked).toBe(false);
    });

    it("goes back to the scope the settings read found when a write is refused", async () => {
      vi.mocked(backend.getSettings).mockResolvedValue(settings({ collection_owner_scope: "own" }));
      vi.mocked(backend.setCollectionOwnerScope).mockResolvedValueOnce({ success: false, message: "No" });
      const { container } = await openCollections();
      expect(ownerSwitch(container).checked).toBe(false);

      await click(ownerSwitch(container));

      expect(ownerSwitch(container).checked).toBe(false);
    });
  });

  describe("a refused write after one that was stored", () => {
    const REFUSED = { success: false, message: "Migration pending" };

    it("leaves a row at the value a later Enable all gave it when an earlier single switch is refused", async () => {
      const single = held();
      const batch = held();
      vi.mocked(backend.saveCollectionSync).mockReturnValueOnce(single.promise);
      vi.mocked(backend.saveCollectionsSync).mockReturnValueOnce(batch.promise);
      const { container } = await openCollections();
      // Finished is off in the read; switched on on its own, then Enable all.
      await click(checkbox(tableRow(container, "Finished")));
      typeSearch(container, "i");
      await click(button(container, "Enable all"));

      await single.answer({ success: false, message: "Migration pending" });
      expect(checkbox(tableRow(container, "Finished")).checked).toBe(true);
      expect(paneStatus(container)).toBeNull();

      await batch.answer({ success: true });
      expect(checkbox(tableRow(container, "Finished")).checked).toBe(true);
      expect(paneStatus(container)).toBeNull();
    });

    it("puts a row back to what its last stored write set", async () => {
      vi.mocked(backend.saveCollectionSync).mockResolvedValueOnce({ success: true }).mockResolvedValueOnce(REFUSED);
      const { container } = await openCollections();
      await click(checkbox(tableRow(container, "Finished")));
      await click(checkbox(tableRow(container, "Finished")));
      expect(checkbox(tableRow(container, "Finished")).checked).toBe(true);
    });

    it("puts the owner switch back to what its last stored write set", async () => {
      vi.mocked(backend.setCollectionOwnerScope)
        .mockResolvedValueOnce({ success: true })
        .mockResolvedValueOnce(REFUSED);
      const { container } = await openCollections();
      await click(ownerSwitch(container));
      await click(ownerSwitch(container));
      expect(ownerSwitch(container).checked).toBe(false);
    });

    it("puts a row back to what a stored Enable all set", async () => {
      vi.mocked(backend.saveCollectionSync).mockResolvedValueOnce(REFUSED);
      const { container } = await openCollections();
      typeSearch(container, "i");
      await click(button(container, "Enable all"));
      await click(checkbox(tableRow(container, "Finished")));
      expect(checkbox(tableRow(container, "Finished")).checked).toBe(true);
    });
  });

  describe("an owner nothing has established yet", () => {
    it("names the owner rather than you, and stays listed with the owner switch off", async () => {
      vi.mocked(backend.getSettings).mockResolvedValue(settings({ collection_owner_scope: "own" }));
      const shared = coll({ id: "n", name: "Shared", is_own: null, owner_username: "mara" });
      const { container } = await openCollections([shared]);
      expect(tableNames(container)).toEqual(["Shared"]);
      expect(cellTexts(tableRow(container, "Shared"))[1]).toBe("mara");
    });
  });
});
