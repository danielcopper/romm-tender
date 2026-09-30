import { readFileSync } from "node:fs";
import { describe, it, expect } from "vitest";
import { foldCollectionName } from "./collectionName";

// The backend's tests/domain/test_collection_name.py reads the same file, so
// both sides are held to the same key for every name in it.
interface FoldEntry {
  name: string;
  fold: string;
  case: string;
}

const FOLDS = JSON.parse(
  readFileSync(`${process.cwd()}/../tests/domain/collection_name_folds.json`, "utf8"),
) as FoldEntry[];

describe("foldCollectionName", () => {
  it.each(FOLDS.map((entry) => [entry.case, entry] as const))(
    "folds to the key both sides agree on: %s",
    (_case, entry) => {
      expect(foldCollectionName(entry.name)).toBe(entry.fold);
    },
  );

  it("keeps names that differ beyond case apart", () => {
    expect(foldCollectionName("7 Up")).not.toBe(foldCollectionName("7 Up Deluxe"));
  });
});
