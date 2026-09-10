import { ESLint } from "eslint";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const QAM_FIXTURE_DIR = path.join(process.cwd(), "src", "components", "qam", "__eslint_fixtures__");
const OFF_SCOPE_FIXTURE_DIR = path.join(process.cwd(), "src", "components", "__eslint_fixtures__");
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

async function ruleMessages(file: string): Promise<string[]> {
  const results = await new ESLint({ cwd: process.cwd() }).lintFiles([file]);
  return results.flatMap((result) => result.messages.map((message) => message.ruleId ?? "<fatal>"));
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
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "badRow.tsx"))).toEqual([RULE_ID]);
  });

  it("tracks an aliased Decky Focusable import", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "aliasedBadRow.tsx"))).toEqual([RULE_ID]);
  });

  it("accepts activation props, the lower-level focusable syntax, and a focusable descendant", async () => {
    expect(await ruleMessages(path.join(QAM_FIXTURE_DIR, "validRows.tsx"))).toEqual([]);
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
      RULE_ID,
      RULE_ID,
      RULE_ID,
      RULE_ID,
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
      "DangerZone.tsx",
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
        expect(await configuredRule(path.join(process.cwd(), "src", "components", file))).toEqual([2]);
      }),
    );
    expect(await configuredRule(path.join(process.cwd(), "src", "components", "CustomPlayButton.tsx"))).toBeUndefined();
  });
}, 60_000);
