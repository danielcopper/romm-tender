// Prints the panel's half of the Steam-internals list as JSON, for
// `scripts/watch_steam_internals.py`, which reads the backend's half itself.
// Node imports the TypeScript module directly by stripping its types, which
// works because the module imports nothing but a type.
import { pathToFileURL } from "node:url";

import { STEAM_FEATURES, STEAM_INTERNALS } from "../src/boot/steamInternals.ts";

/** Each entry as the watch reads it: its path, its group, its exemption and the losses in the notice's words. */
export const panelHalf = () =>
  STEAM_INTERNALS.map((entry) => ({
    path: entry.path,
    oneOf: entry.oneOf ?? null,
    notSpelledBySteam: entry.notSpelledBySteam ?? null,
    devBuildOnly: entry.devBuildOnly === true,
    losses: entry.features.map((feature) => STEAM_FEATURES[feature]),
  }));

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  process.stdout.write(`${JSON.stringify(panelHalf(), null, 2)}\n`);
}
