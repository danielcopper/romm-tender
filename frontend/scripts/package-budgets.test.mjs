import { describe, expect, it } from "vitest";

import { budgetProblems, packageBytes, packageOf } from "./package-budgets.mjs";

const STORE = "/repo/frontend/node_modules/.pnpm";

describe("packageOf", () => {
  it("names the package under pnpm's store layout, not the store", () => {
    expect(packageOf(`${STORE}/react-icons@5.6.0/node_modules/react-icons/fa/index.mjs`)).toBe("react-icons");
  });

  it("names a scoped package by its scope and its name", () => {
    expect(packageOf(`${STORE}/@decky+ui@4.12.0/node_modules/@decky/ui/dist/webpack.js`)).toBe("@decky/ui");
  });

  it("names the innermost package when one is nested in another", () => {
    expect(packageOf("/repo/frontend/node_modules/outer/node_modules/inner/lib/index.js")).toBe("inner");
  });

  it("reads a Windows path the same way", () => {
    expect(packageOf("C:\\repo\\frontend\\node_modules\\@decky\\ui\\dist\\index.js")).toBe("@decky/ui");
  });

  it("answers null for the panel's own code", () => {
    expect(packageOf("/repo/frontend/src/index.tsx")).toBeNull();
  });

  it("answers null for a module a plugin made up, even one naming a package's path", () => {
    expect(packageOf("\0virtual:tender-bundle-kind")).toBeNull();
    expect(packageOf(`\0${STORE}/react-icons@5.6.0/node_modules/react-icons/lib/index.mjs?commonjs-proxy`)).toBeNull();
  });
});

describe("packageBytes", () => {
  it("sums each package's modules and leaves the panel's own code out", () => {
    expect(
      packageBytes([
        [`${STORE}/@decky+ui@4.12.0/node_modules/@decky/ui/dist/a.js`, 30],
        [`${STORE}/@decky+ui@4.12.0/node_modules/@decky/ui/dist/b.js`, 12],
        [`${STORE}/react-icons@5.6.0/node_modules/react-icons/fa/index.mjs`, 7],
        ["/repo/frontend/src/index.tsx", 900],
        ["\0virtual:tender-bundle-kind", 33],
      ]),
    ).toEqual({ "@decky/ui": 42, "react-icons": 7 });
  });

  it("leaves out a package whose every module rendered to nothing", () => {
    expect(packageBytes([[`${STORE}/react-icons@5.6.0/node_modules/react-icons/fa/index.mjs`, 0]])).toEqual({});
  });
});

const BUDGETS = { "index.js": { "@decky/ui": 100, "react-icons": 50 } };
const DIGESTS = { "index.js": "abc" };
const recordOf = (packages, sha256 = "abc") => ({ "index.js": { sha256, packages } });

describe("budgetProblems", () => {
  it("finds nothing when every package is within its budget", () => {
    expect(
      budgetProblems({
        budgets: BUDGETS,
        record: recordOf({ "@decky/ui": 100, "react-icons": 1 }),
        digests: DIGESTS,
      }),
    ).toEqual([]);
  });

  it("refuses a package over its budget", () => {
    expect(
      budgetProblems({ budgets: BUDGETS, record: recordOf({ "@decky/ui": 101, "react-icons": 1 }), digests: DIGESTS }),
    ).toEqual(["index.js: @decky/ui puts 101 B into it, over its budget of 100 B."]);
  });

  it("refuses a package with no budget in that bundle", () => {
    const [problem, ...rest] = budgetProblems({
      budgets: BUDGETS,
      record: recordOf({ "@decky/ui": 1, "react-icons": 1, lodash: 4 }),
      digests: DIGESTS,
    });
    expect(rest).toEqual([]);
    expect(problem).toMatch(/^index\.js: lodash puts 4 B into it and has no budget/);
  });

  it("refuses a budget for a package the bundle no longer carries", () => {
    const [problem, ...rest] = budgetProblems({
      budgets: BUDGETS,
      record: recordOf({ "@decky/ui": 1 }),
      digests: DIGESTS,
    });
    expect(rest).toEqual([]);
    expect(problem).toMatch(/^index\.js: react-icons has a budget but puts nothing into it/);
  });

  it("refuses when there is no record at all", () => {
    expect(budgetProblems({ budgets: BUDGETS, record: null, digests: DIGESTS })).toEqual([
      expect.stringMatching(/^there is no package record/),
    ]);
  });

  it("refuses a record of an older build than dist/ holds, and judges none of its packages", () => {
    expect(
      budgetProblems({ budgets: BUDGETS, record: recordOf({ "@decky/ui": 999 }, "old"), digests: DIGESTS }),
    ).toEqual([expect.stringMatching(/^the record of index\.js describes an older build/)]);
  });

  it("refuses a recorded bundle that dist/ does not hold", () => {
    expect(
      budgetProblems({ budgets: BUDGETS, record: recordOf({ "@decky/ui": 1 }), digests: { "index.js": null } }),
    ).toEqual([expect.stringMatching(/^index\.js is not in dist\//)]);
  });

  it("refuses a bundle that has budgets and no record", () => {
    expect(budgetProblems({ budgets: BUDGETS, record: {}, digests: DIGESTS })).toEqual([
      expect.stringMatching(/^index\.js has budgets but no record/),
    ]);
  });

  it("refuses a recorded bundle that has no entry in the budgets", () => {
    expect(
      budgetProblems({
        budgets: BUDGETS,
        record: { ...recordOf({ "@decky/ui": 1, "react-icons": 1 }), "other.js": { sha256: "x", packages: {} } },
        digests: { ...DIGESTS, "other.js": "x" },
      }),
    ).toEqual([expect.stringMatching(/^other\.js is recorded but has no entry in package-budgets\.json/)]);
  });
});
