import { describe, it, expect } from "vitest";
import {
  getFiberFromDom,
  resolveFiberTypeName,
  getFiberComponentName,
  getFiberDisplayName,
  findAncestorFiber,
  getAppIdFromFiber,
  type MinimalFiber,
} from "./fiberInspector";

describe("fiberInspector", () => {
  describe("getFiberFromDom", () => {
    it("returns null for non-elements or null/undefined", () => {
      expect(getFiberFromDom(null)).toBeNull();
      expect(getFiberFromDom(undefined)).toBeNull();
      expect(getFiberFromDom({} as unknown as Element)).toBeNull();
    });

    it("retrieves fiber instance starting with __reactFiber$", () => {
      const el = document.createElement("div");
      const mockFiber: MinimalFiber = { type: "div", memoizedProps: { id: "test" } };
      (el as unknown as Record<string, unknown>)["__reactFiber$abc123"] = mockFiber;

      expect(getFiberFromDom(el)).toBe(mockFiber);
    });

    it("retrieves fiber instance starting with __reactInternalInstance$", () => {
      const el = document.createElement("div");
      const mockFiber: MinimalFiber = { type: "div" };
      (el as unknown as Record<string, unknown>)["__reactInternalInstance$xyz789"] = mockFiber;

      expect(getFiberFromDom(el)).toBe(mockFiber);
    });
  });

  describe("resolveFiberTypeName", () => {
    it("returns string as-is", () => {
      expect(resolveFiberTypeName("PlayButton")).toBe("PlayButton");
    });

    it("extracts name or displayName from function component", () => {
      function CustomPlayBar() {}
      expect(resolveFiberTypeName(CustomPlayBar)).toBe("CustomPlayBar");

      const AnonFn = () => null;
      AnonFn.displayName = "ExplicitPlayBar";
      expect(resolveFiberTypeName(AnonFn)).toBe("ExplicitPlayBar");
    });

    it("extracts displayName or name from object", () => {
      expect(resolveFiberTypeName({ displayName: "OverviewPanel" })).toBe("OverviewPanel");
      expect(resolveFiberTypeName({ name: "ActionSection" })).toBe("ActionSection");
      expect(resolveFiberTypeName({ type: { displayName: "InnerMemo" } })).toBe("InnerMemo");
    });

    it("returns null for null, undefined, or empty objects", () => {
      expect(resolveFiberTypeName(null)).toBeNull();
      expect(resolveFiberTypeName(undefined)).toBeNull();
      expect(resolveFiberTypeName({})).toBeNull();
    });
  });

  describe("getFiberComponentName and getFiberDisplayName", () => {
    it("skips native HTML tag names and returns custom component name", () => {
      const parentFiber: MinimalFiber = {
        type: { displayName: "AppDetailsOverviewPanel" },
      };
      const childFiber: MinimalFiber = {
        type: "div",
        return: parentFiber,
      };

      expect(getFiberComponentName(childFiber)).toBe("AppDetailsOverviewPanel");
    });

    it("resolves display name from DOM element with attached fiber", () => {
      const el = document.createElement("div");
      const parentFiber: MinimalFiber = {
        type: function PlayBar() {},
      };
      const fiber: MinimalFiber = {
        type: "div",
        return: parentFiber,
      };
      (el as unknown as Record<string, unknown>)["__reactFiber$123"] = fiber;

      expect(getFiberDisplayName(el)).toBe("PlayBar");
    });

    it("returns null when no non-tag component is found within depth", () => {
      const fiber: MinimalFiber = { type: "span", return: { type: "div" } };
      expect(getFiberComponentName(fiber, 5)).toBeNull();
    });
  });

  describe("findAncestorFiber", () => {
    it("finds ancestor matching predicate", () => {
      const target: MinimalFiber = { type: "Target", memoizedProps: { match: true } };
      const chain: MinimalFiber = {
        type: "Child",
        return: {
          type: "Middle",
          return: target,
        },
      };

      const found = findAncestorFiber(chain, (f) => f.memoizedProps?.match === true);
      expect(found).toBe(target);
    });

    it("returns null if predicate never matches within maxDepth", () => {
      const chain: MinimalFiber = { type: "Child", return: { type: "Parent" } };
      expect(findAncestorFiber(chain, (f) => f.type === "NotFound", 5)).toBeNull();
    });
  });

  describe("getAppIdFromFiber", () => {
    it("extracts appId from overview.appid prop", () => {
      const el = document.createElement("div");
      (el as unknown as Record<string, unknown>)["__reactFiber$1"] = {
        type: "div",
        return: {
          type: "OverviewWrapper",
          memoizedProps: { overview: { appid: 424242 } },
        },
      };

      expect(getAppIdFromFiber(el)).toBe(424242);
    });

    it("extracts appId from direct appId prop", () => {
      const el = document.createElement("div");
      (el as unknown as Record<string, unknown>)["__reactFiber$2"] = {
        type: "div",
        return: {
          type: "GameDetail",
          memoizedProps: { appId: 88888 },
        },
      };

      expect(getAppIdFromFiber(el)).toBe(88888);
    });

    it("extracts appId from details.appid prop", () => {
      const el = document.createElement("div");
      (el as unknown as Record<string, unknown>)["__reactFiber$3"] = {
        type: "div",
        return: {
          type: "DetailsSection",
          memoizedProps: { details: { appid: 99999 } },
        },
      };

      expect(getAppIdFromFiber(el)).toBe(99999);
    });

    it("returns null when no appId prop exists in hierarchy", () => {
      const el = document.createElement("div");
      (el as unknown as Record<string, unknown>)["__reactFiber$4"] = {
        type: "div",
        memoizedProps: { className: "nothing" },
      };

      expect(getAppIdFromFiber(el)).toBeNull();
    });
  });
});
