/**
 * QAM panel navigation — the set of pages the panel can show. Anything that
 * names a page the QAM router can land on lives here.
 */

/** Every page the QAM panel can show. A surface that cannot navigate to itself
 *  narrows this with `Exclude<Page, "...">` rather than restating the union. */
export type Page = "main" | "sync" | "settings" | "library" | "data" | "downloads";

/**
 * The Settings page's sections, in the order its list shows them — the first is
 * the one a navigation that names none opens on.
 *
 * The order and the union are one declaration so they cannot drift: a section
 * added here is a section the page's list has to label, and the type stops a
 * navigation naming one that does not exist.
 */
export const SETTINGS_SECTIONS = ["connections", "save-sync", "controller", "steam-library", "advanced"] as const;

/** One section of the Settings page, as a navigation may name it. */
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

/**
 * Where a navigation lands: a page, or the Settings page opened on one of its
 * sections.
 *
 * The section rides on the page rather than travelling beside it, so no target
 * can pair a section with a page that has none — the notices on Main each name
 * the section that holds the action they are about.
 */
export type NavTarget = Exclude<Page, "main"> | { page: "settings"; section: SettingsSection };
