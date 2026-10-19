import { describe, expect, it, vi } from "vitest";

import { START_ANCHOR, insertDesktopSurfaceStart } from "./start-desktop-surface.mjs";

const INDEX = "/repo/frontend/src/index.tsx";
const PANEL = `import { x } from "./x";\nfunction start() {\n  ${START_ANCHOR}\n  other();\n}\n`;

const context = () => ({
  error: vi.fn((message) => {
    throw new Error(message);
  }),
});

/** Run one build through the plugin: its start, a transform of each module, and its end. */
const build = (modules) => {
  const surfaceStart = insertDesktopSurfaceStart();
  const ctx = context();
  surfaceStart.buildStart.call(ctx);
  const results = modules.map(([code, id]) => surfaceStart.transform.call(ctx, code, id));
  surfaceStart.buildEnd.call(ctx);
  return results;
};

describe("insertDesktopSurfaceStart", () => {
  it("starts the desktop surface right after the anchor in src/index.tsx, moving no line", () => {
    const [result] = build([[PANEL, INDEX]]);
    expect(result.code).toBe(
      `import { x } from "./x";\nfunction start() {\n  ${START_ANCHOR} startDesktopSurface();\n  other();\n}\n` +
        `\nimport { startDesktopSurface } from "./desktop";\n`,
    );
    const lines = result.code.split("\n");
    PANEL.split("\n").forEach((line, i) => expect(lines[i].startsWith(line)).toBe(true));
  });

  it("finds src/index.tsx under a Windows path too", () => {
    const [result] = build([[PANEL, "C:\\repo\\frontend\\src\\index.tsx"]]);
    expect(result.code).toContain("startDesktopSurface();");
  });

  it("leaves every other module alone", () => {
    const results = build([
      [PANEL, "/repo/frontend/src/bigpicture/index.tsx"],
      [PANEL, "/repo/frontend/src/boot/globals.ts"],
      [PANEL, INDEX],
    ]);
    expect(results.slice(0, 2)).toEqual([null, null]);
  });

  it("fails the build when src/index.tsx no longer holds the anchor", () => {
    expect(() => build([["function start() {}\n", INDEX]])).toThrow(START_ANCHOR);
  });

  it("fails the build when src/index.tsx holds the anchor more than once", () => {
    expect(() => build([[`// ${START_ANCHOR} runs below\n${PANEL}`, INDEX]])).toThrow("2 times");
  });

  it("fails the build when it never reaches src/index.tsx", () => {
    expect(() => build([[PANEL, "/repo/frontend/src/main.tsx"]])).toThrow("never reached src/index.tsx");
  });

  it("does not add its own failure to a build that already failed", () => {
    const surfaceStart = insertDesktopSurfaceStart();
    const ctx = context();
    surfaceStart.buildStart.call(ctx);
    expect(() => surfaceStart.buildEnd.call(ctx, new Error("a syntax error"))).not.toThrow();
  });
});
