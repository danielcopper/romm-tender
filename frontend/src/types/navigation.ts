/**
 * QAM panel navigation — the set of pages the panel can show. Anything that
 * names a page the QAM router can land on lives here.
 */

/** Every page the QAM panel can show. A surface that cannot navigate to itself
 *  narrows this with `Exclude<Page, "...">` rather than restating the union. */
export type Page = "main" | "sync" | "settings" | "library" | "data" | "downloads";

/** One section of the Settings page, as a navigation may name it. The order the
 *  page lists them in is `SETTINGS_SECTIONS` (`utils/settingsSections.ts`),
 *  which is checked against this union there. */
export type SettingsSection =
  | "connections"
  | "save-sync"
  | "controller"
  | "steam-library"
  | "emulator-sources"
  | "tender-folder"
  | "updates"
  | "interface"
  | "advanced";

/**
 * Where a navigation lands: a page, or the Settings page opened on one of its
 * sections.
 *
 * The section rides on the page rather than travelling beside it, so no target
 * can pair a section with a page that has none — the notices on Main each name
 * the section that holds the action they are about.
 */
export type NavTarget = Exclude<Page, "main"> | { page: "settings"; section: SettingsSection };
