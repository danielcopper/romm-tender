import type { SettingsSection } from "../types";

/**
 * The Settings page's sections, in the order its list shows them — the first is
 * the one a navigation that names none opens on.
 */
export const SETTINGS_SECTIONS = [
  "connections",
  "save-sync",
  "controller",
  "steam-library",
  "emulator-sources",
  "updates",
  "interface",
  "advanced",
] as const satisfies readonly SettingsSection[];

// `satisfies` refuses a section the union does not name; this refuses a section
// the union names and the list leaves out, and the error names that section.
// Exported only because `noUnusedLocals` refuses a type nothing reads.
type NoneUnlisted<Unlisted extends never> = Unlisted;
export type EverySectionListed = NoneUnlisted<Exclude<SettingsSection, (typeof SETTINGS_SECTIONS)[number]>>;
