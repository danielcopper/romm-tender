/**
 * When two Steam collection names are the same collection. The rule, and why it
 * is this one, is docs/architecture/steam-non-steam-shortcuts.md, "Name identity
 * is case-insensitive"; the backend applies it as `fold_collection_name`
 * (`backend/domain/collection_name.py`).
 */

/** The key under which `name` is one Steam collection with every name sharing it. */
export function foldCollectionName(name: string): string {
  return name.toLowerCase().toUpperCase().toLowerCase();
}
