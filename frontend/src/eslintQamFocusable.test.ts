import { ESLint } from "eslint";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const QAM_FIXTURE_DIR = path.join(process.cwd(), "src", "bigpicture", "layout", "__eslint_fixtures__");
const OFF_SCOPE_FIXTURE_DIR = path.join(process.cwd(), "src", "bigpicture", "__eslint_fixtures__");
const RULE_ID = "tender/qam-focusable-row";

const FIXTURES: [dir: string, name: string, source: string][] = [
  [
    QAM_FIXTURE_DIR,
    "badRow.tsx",
    'import { Focusable } from "@decky/ui";\nexport const BadRow = () => <Focusable><div>Unreadable</div></Focusable>;\n',
  ],
  [
    QAM_FIXTURE_DIR,
    "aliasedBadRow.tsx",
    'import { Focusable as DeckyFocusable } from "@decky/ui";\nexport const BadRow = () => <DeckyFocusable><span>Unreadable</span></DeckyFocusable>;\n',
  ],
  [
    QAM_FIXTURE_DIR,
    "validRows.tsx",
    `import { Focusable } from "@decky/ui";
export const Activate = () => <Focusable onActivate={() => {}}><div>Reachable</div></Focusable>;
export const OkButton = () => <Focusable onOKButton={() => {}}><div>Reachable</div></Focusable>;
export const Explicit = () => <Focusable focusable={true}><div>Reachable</div></Focusable>;
export const StaticSpread = () => <Focusable {...{ onActivate: () => {} }}><div>Reachable</div></Focusable>;
export const Descendant = () => <Focusable><div><Focusable onActivate={() => {}}>Reachable</Focusable></div></Focusable>;
`,
  ],
  [
    QAM_FIXTURE_DIR,
    "navStopRows.tsx",
    `import { Focusable } from "@decky/ui";
export const IfEmpty = () => <Focusable focusableIfEmpty={true}><div>Reachable</div></Focusable>;
export const IfEmptyBare = () => <Focusable focusableIfEmpty><div>Reachable</div></Focusable>;
export const IfEmptySpread = () => <Focusable {...{ focusableIfEmpty: true }}><div>Reachable</div></Focusable>;
`,
  ],
  [
    QAM_FIXTURE_DIR,
    "domFocusDescendants.tsx",
    `import { Focusable } from "@decky/ui";
export const TabStopChild = () => <Focusable><div role="button" tabIndex={0}>Reachable</div></Focusable>;
export const TabStopChildSpread = () => <Focusable><div role="button" {...{ tabIndex: 0 }}>Reachable</div></Focusable>;
export const NativeButtonChild = () => <Focusable><button type="button">Reachable</button></Focusable>;
export const AnchorChild = () => <Focusable><a href="https://example.com">Reachable</a></Focusable>;
export const InputChild = () => <Focusable><input aria-label="Reachable" /></Focusable>;
export const SelectChild = () => <Focusable><select aria-label="Reachable"><option>Reachable</option></select></Focusable>;
export const TextareaChild = () => <Focusable><textarea aria-label="Reachable" /></Focusable>;
`,
  ],
  [
    QAM_FIXTURE_DIR,
    "unreachableRows.tsx",
    `import { Focusable } from "@decky/ui";
export const TabStopRow = () => <Focusable tabIndex={0}><div>Unreadable</div></Focusable>;
export const TabStopRowSpread = () => <Focusable {...{ tabIndex: 0 }}><div>Unreadable</div></Focusable>;
export const FocusableFalse = () => <Focusable focusable={false}><div>Unreadable</div></Focusable>;
export const IfEmptyFalse = () => <Focusable focusableIfEmpty={false}><div>Unreadable</div></Focusable>;
export const FalseInSpread = () => <Focusable {...{ focusable: false }}><div>Unreadable</div></Focusable>;
export const CastFalse = () => <Focusable focusable={false as boolean}><div>Unreadable</div></Focusable>;
export const BangFalse = () => <Focusable focusable={false!}><div>Unreadable</div></Focusable>;
export const InertOnHostChild = () => <Focusable><div {...{ focusableIfEmpty: true }}>Unreadable</div></Focusable>;
`,
  ],
  [
    QAM_FIXTURE_DIR,
    "cancelOnlyRows.tsx",
    `import { Focusable } from "@decky/ui";
export const Cancel = () => <Focusable onCancel={() => {}}><div>Unreadable</div></Focusable>;
export const CancelButton = () => <Focusable onCancelButton={() => {}}><div>Unreadable</div></Focusable>;
export const CancelSpread = () => <Focusable {...{ onCancelButton: () => {} }}><div>Unreadable</div></Focusable>;
`,
  ],
  [
    QAM_FIXTURE_DIR,
    "conservativeRows.tsx",
    `import type { ReactNode } from "react";
import { Focusable } from "@decky/ui";
export const Dynamic = ({ children }: { children: ReactNode }) => <Focusable>{children}</Focusable>;
export const Spread = (props: { onActivate?: () => void }) => <Focusable {...props}><div>Maybe reachable</div></Focusable>;
`,
  ],
  [
    QAM_FIXTURE_DIR,
    "localFocusable.tsx",
    "const Focusable = ({ children }: { children: string }) => <div>{children}</div>;\nexport const Local = () => <Focusable>Unrelated</Focusable>;\n",
  ],
  [
    QAM_FIXTURE_DIR,
    "shadowedFocusable.tsx",
    `import type { ComponentType, PropsWithChildren } from "react";
import { Focusable } from "@decky/ui";
export const Imported = () => <Focusable onActivate={() => {}}>Reachable</Focusable>;
export const Local = ({ Focusable }: { Focusable: ComponentType<PropsWithChildren> }) => <Focusable>Unrelated</Focusable>;
`,
  ],
  [
    QAM_FIXTURE_DIR,
    "staticExpressions.tsx",
    `import { Focusable } from "@decky/ui";
const value = "row";
export const Literal = () => <Focusable>{"Unreadable"}</Focusable>;
export const StaticSpread = () => <Focusable {...{ className: "row" }}><div>Unreadable</div></Focusable>;
export const Template = () => <Focusable>{\`Unreadable \${value}\`}</Focusable>;
export const Satisfies = () => <Focusable {...({ className: value } satisfies Record<string, string>)}><div>Unreadable</div></Focusable>;
`,
  ],
  [
    OFF_SCOPE_FIXTURE_DIR,
    "gameDetailRoute.tsx",
    'import { Focusable } from "@decky/ui";\nexport const GameDetail = () => <Focusable><div>Outside QAM</div></Focusable>;\n',
  ],
];

/**
 * Every message as `<rule>:<line>`. The line is part of it because a fixture holds
 * one shape per line: a bare list of rule ids passes when one shape stops
 * reporting and another starts, which is the only thing these fixtures can drift
 * into. A message from any other rule shows up here too, so an assertion cannot go
 * green on a `jsx-a11y` complaint standing in for ours.
 */
async function ruleMessages(file: string): Promise<string[]> {
  const results = await new ESLint({ cwd: process.cwd() }).lintFiles([file]);
  return results.flatMap((result) =>
    result.messages.map((message) => `${message.ruleId ?? "<fatal>"}:${message.line}`),
  );
}

async function configuredRule(file: string): Promise<unknown> {
  const config = await new ESLint({ cwd: process.cwd() }).calculateConfigForFile(file);
  return config?.rules?.[RULE_ID];
}

describe("QAM Focusable row rule", () => {
  beforeAll(async () => {
    await mkdir(QAM_FIXTURE_DIR, { recursive: true });
    await mkdir(OFF_SCOPE_FIXTURE_DIR, { recursive: true });
    await Promise.all(FIXTURES.map(([dir, name, source]) => writeFile(path.join(dir, name), source, "utf8")));
  });

  afterAll(async () => {
    await rm(QAM_FIXTURE_DIR, { recursive: true, force: true });
    await rm(OFF_SCOPE_FIXTURE_DIR, { recursive: true, force: true });
  });

  it("reports a static row without a focus stop", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "badRow.tsx"))).toEqual([`${RULE_ID}:2`]);
  });

  it("tracks an aliased Decky Focusable import", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "aliasedBadRow.tsx"))).toEqual([`${RULE_ID}:2`]);
  });

  it("accepts activation props, the lower-level focusable syntax, and a focusable descendant", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "validRows.tsx"))).toEqual([]);
  });

  it("accepts a row declaring the nav option that makes an empty node a stop", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "navStopRows.tsx"))).toEqual([]);
  });

  it("accepts a container holding a descendant the browser can focus", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "domFocusDescendants.tsx"))).toEqual([]);
  });

  it("reports a row whose own tabIndex, an explicit false, or a nav option on a plain div is all it has", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "unreachableRows.tsx"))).toEqual([
      `${RULE_ID}:2`,
      `${RULE_ID}:3`,
      `${RULE_ID}:4`,
      `${RULE_ID}:5`,
      `${RULE_ID}:6`,
      `${RULE_ID}:7`,
      `${RULE_ID}:8`,
      `${RULE_ID}:9`,
    ]);
  });

  it("still reports a row whose only handler answers cancel", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "cancelOnlyRows.tsx"))).toEqual([
      `${RULE_ID}:2`,
      `${RULE_ID}:3`,
      `${RULE_ID}:4`,
    ]);
  });

  it("accepts dynamic children and unknown spreads conservatively", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "conservativeRows.tsx"))).toEqual([]);
  });

  it("ignores an unrelated local component named Focusable", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "localFocusable.tsx"))).toEqual([]);
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "shadowedFocusable.tsx"))).toEqual([]);
  });

  it("reports statically known non-focusable expressions and spreads", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "staticExpressions.tsx"))).toEqual([
      `${RULE_ID}:3`,
      `${RULE_ID}:4`,
      `${RULE_ID}:5`,
      `${RULE_ID}:6`,
    ]);
  });

  it("does not impose the QAM rule on game-detail components", async () => {
    expect(await ruleMessages(path.join(OFF_SCOPE_FIXTURE_DIR, "gameDetailRoute.tsx"))).toEqual([]);
  });

  it("covers every QAM page root and shared QAM-only module without reaching game-detail routes", async () => {
    const qamFiles = [
      "MainPage.tsx",
      "SyncPage.tsx",
      "LibraryPage.tsx",
      "SettingsPage.tsx",
      "DataManagementPage.tsx",
      "RemovedGamesCleanup.tsx",
      "DownloadQueue.tsx",
      "SessionBudgetBanner.tsx",
      "MigrationBlockedPage.tsx",
      "SettingsResetBanner.tsx",
      "PlaytimeScopeBanner.tsx",
      "DownloadProgressRow.tsx",
      "LoadingRow.tsx",
    ];
    await Promise.all(
      qamFiles.map(async (file) => {
        expect(await configuredRule(path.join(process.cwd(), "src", "bigpicture", file))).toEqual([2]);
      }),
    );
    expect(await configuredRule(path.join(process.cwd(), "src", "bigpicture", "CustomPlayButton.tsx"))).toBeUndefined();
  });
}, 60_000);
