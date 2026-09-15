// The one place the two BIOS surfaces get their row wording from, so the cases
// are pinned here once rather than twice in two component suites. What each
// surface does with the result — suffix, description, or nothing — is pinned
// with that surface.

import { describe, it, expect } from "vitest";
import type { FirmwareChecked, FirmwareDeclaredKind } from "../types";
import { biosFileDescription, biosFileNote, type BiosNoteRow } from "./biosFileNote";

/** The sentence half, for the cases that are about wording alone. */
const noteOf = (row: BiosNoteRow) => biosFileNote(row).note;

/** LRPS2's `pcsx2/bios` row, with the verdict a test wants to word. */
const folder = (satisfied: boolean | null, caveats: string[] = [], images: string[] = []) => ({
  downloaded: true,
  on_server: false,
  declared_kind: "directory" as const,
  satisfied,
  caveats,
  images,
});

describe("biosFileNote", () => {
  it("says nothing about a plain library file, present or missing", () => {
    // Its state is the row's own to show; the surfaces disagree on how, so the
    // shared note stays out of it.
    expect(noteOf({ downloaded: true, on_server: true, satisfied: true })).toBe("");
    expect(noteOf({ downloaded: false, on_server: true, satisfied: false })).toBe("");
  });

  it("names the distribution that supplied the file", () => {
    // Verbatim: the name is the resolver's own display form for the
    // distribution, so anything prettier here would be a name we invented.
    expect(noteOf({ downloaded: true, on_server: false, supplied_by: "RetroDECK" })).toBe("provided by RetroDECK");
  });

  it("prefers the distribution over the library note", () => {
    // "not in your RomM library" is true of codehandler.bin and useless: no
    // library will ever hold it, and the emulator already did put it there.
    expect(noteOf({ downloaded: true, on_server: false, supplied_by: "RetroDECK" })).not.toContain("RomM");
  });

  it("hands the images over as a list, one line each, with no sentence over them", () => {
    // A list, not a joined sentence: three PS2 dumps ran to ~150 characters,
    // which wrapped the row and left its status dot on a line of its own. The
    // core needs exactly one of them, so none is labelled required or optional
    // and none is marked as the one that will load — which of them LRPS2 picks
    // is a core option this plugin does not read.
    const images = ["Europe  v02.00(14/06/2004)", "Japan   v02.00(14/06/2004)"];

    const words = biosFileNote(folder(true, ["firmware-image-identified"], images));

    expect(words.lines).toEqual(images);
    expect(words.note).toBe("");
  });

  it("still says a satisfied folder holds an image when none was named", () => {
    expect(biosFileNote(folder(true))).toEqual({ note: "holds a BIOS image", lines: [], fromLibrary: false });
  });

  it("says an unmet folder holds no image", () => {
    expect(noteOf(folder(false, ["firmware-directory-holds-no-image"]))).toBe("holds no BIOS image");
    expect(noteOf(folder(false, ["firmware-directory-holds-no-candidate"]))).toBe("holds no BIOS image");
  });

  it("leaves an absent folder to the surfaces' own word for missing", () => {
    // Nothing was listed, so there is no finding about contents to report —
    // the row is simply not there, like any other.
    expect(noteOf({ ...folder(false), downloaded: false })).toBe("");
  });

  it("words each withheld folder verdict off its own code", () => {
    expect(noteOf(folder(null, ["firmware-image-contradicted"]))).toBe("holds an image that could not be confirmed");
    expect(noteOf(folder(null, ["firmware-scan-incomplete"]))).toBe("its contents could not be read in full");
    expect(noteOf(folder(null, ["firmware-unreadable"]))).toBe("its contents could not be read in full");
    // No code at all is what a read that failed and a question nobody asked
    // both leave behind — the payload cannot tell them apart, and the fallback
    // is written not to try.
    expect(noteOf(folder(null))).toBe("its contents could not be checked");
  });

  it("says the wrong shape is at a destination, in both directions", () => {
    expect(noteOf({ ...folder(false), caveats: ["firmware-path-not-a-directory"] })).toBe(
      "a file is here, where the emulator opens a folder",
    );
    expect(
      noteOf({
        downloaded: true,
        on_server: true,
        declared_kind: "file",
        satisfied: null,
        caveats: ["firmware-path-obstructed"],
      }),
    ).toBe("a folder is here, where the emulator opens a file");
  });

  it("says a file's destination could not be read, rather than leaving it to read as absent", () => {
    // The row is red and counted unmet either way — what the plugin cannot read
    // the emulator cannot open — so the note says the READ failed and claims
    // nothing about whether the file is there.
    expect(
      noteOf({
        downloaded: false,
        on_server: true,
        declared_kind: "file",
        satisfied: false,
        caveats: ["firmware-path-inaccessible"],
      }),
    ).toBe("its location could not be read");
  });

  describe("what became of a file's bytes", () => {
    // Three of the resolver's eight `checked` values are three different things
    // behind one verdict, and the surfaces said "could not be checked" about all
    // of them. Only one of the three is that.
    /** A declared file row carrying the reading the payload states. */
    const file = (satisfied: boolean | null, checked: FirmwareChecked | null): BiosNoteRow => ({
      downloaded: true,
      on_server: true,
      declared_kind: "file",
      satisfied,
      checked,
    });

    it("says the emulator read the file and does not know it, never that it could not be read", () => {
      // Reachable, and no failure: DuckStation boots such an image and calls it
      // an unknown BIOS. The verdict stays withheld — the read is neither proof
      // nor refutation — but a read DID happen, so the old sentence was untrue.
      const note = noteOf(file(null, "unrecognised"));

      expect(note).toBe("the emulator does not recognise this file");
      expect(note).not.toContain("could not");
    });

    it("says the bytes could not be read where they were asked for and did not come back", () => {
      // The one the single old sentence was right about, and it is a statement
      // about THIS process's read rather than about the emulator's.
      expect(noteOf(file(null, "unread"))).toBe("its bytes could not be read");
    });

    it("gives a refused file the reason beside its already-red verdict", () => {
      // `refused` arrives with the verdict settled `false` — the emulator will
      // not open a file of this size at all — so this row is red with or without
      // the note. Without it the page says a file sitting right there is
      // missing.
      expect(noteOf(file(false, "refused"))).toBe("the emulator refuses a file of this size");
    });

    it("says nothing for the values that are the row's ordinary answer", () => {
      // `verified` is the met row and `mismatch` an unmet one the surfaces
      // already word; `null` is a reading that asked no byte question, which is
      // every absent file.
      expect(noteOf(file(true, "verified"))).toBe("");
      expect(noteOf(file(false, "mismatch"))).toBe("");
      expect(noteOf(file(false, null))).toBe("");
      // A payload from before the field existed carries no key at all, which is
      // a third shape and has to read like the stated absence.
      expect(noteOf({ downloaded: true, on_server: true, declared_kind: "file", satisfied: false })).toBe("");
    });

    it("lets the destination's own shape speak first", () => {
      // A folder standing where a file is opened says nothing about bytes,
      // because there were none to read — and the resolver still carries
      // whatever its last content question answered.
      expect(noteOf({ ...file(null, "unread"), caveats: ["firmware-path-obstructed"] })).toBe(
        "a folder is here, where the emulator opens a file",
      );
    });
  });

  it("leaves an unreadable folder to the withheld wording it already has", () => {
    // The same code on a folder row, whose verdict is withheld rather than
    // unmet — the two halves say the read failed in their own words.
    expect(noteOf(folder(null, ["firmware-path-inaccessible"]))).toBe("its contents could not be checked");
  });

  it("keeps the library note for a row nothing else was established for", () => {
    expect(noteOf({ downloaded: true, on_server: false, satisfied: true })).toBe("not in your RomM library");
    expect(noteOf({ downloaded: false, on_server: false, satisfied: false })).toBe("missing, not in your RomM library");
  });

  it("flags the library note as the library one, and no other note", () => {
    // A surface that carries the fact some other way drops the sentence and
    // keeps the rest. Without the flag it would have to re-derive this
    // function's precedence — and a `provided by` row is also `on_server:
    // false`, so keying on that field alone would drop the wrong note.
    expect(biosFileNote({ downloaded: true, on_server: false, satisfied: true }).fromLibrary).toBe(true);
    expect(biosFileNote({ downloaded: false, on_server: false, satisfied: false }).fromLibrary).toBe(true);
    expect(
      biosFileNote({ downloaded: true, on_server: false, supplied_by: "RetroDECK", satisfied: true }).fromLibrary,
    ).toBe(false);
    expect(biosFileNote({ downloaded: true, on_server: true, satisfied: true }).fromLibrary).toBe(false);
    expect(biosFileNote(folder(false, ["firmware-directory-holds-no-image"])).fromLibrary).toBe(false);
  });
});

describe("biosFileDescription", () => {
  // One example per shape the function's own docstring classifies the `.info`
  // corpus into, so the rule is pinned beside the code holding it rather than
  // only through whichever surface happens to render it. The strings are that
  // docstring's own examples; nothing here re-counts the corpus.
  //
  // They are `read` rows because that is what the corpus IS — a libretro core's
  // own `.info`, read off the machine beside it. The rows that are not are the
  // three cases at the end.
  const describedAs = (file_name: string, description: string, declared_kind?: FirmwareDeclaredKind) =>
    biosFileDescription(
      declared_kind
        ? { file_name, description, declared_kind, declaration: "read" }
        : { file_name, description, declaration: "read" },
    );

  it("adds nothing where the description IS the name", () => {
    expect(describedAs("macventure.dat", "macventure.dat")).toBeNull();
  });

  it("keeps the prose after the name, verbatim", () => {
    // Parentheses and all: it is the packager's own wording, and
    // re-punctuating it is a second way to be wrong.
    expect(describedAs("scph5500.bin", "scph5500.bin (PS1 JP BIOS)")).toBe("(PS1 JP BIOS)");
  });

  it("sees the name at the end of a declared path", () => {
    // `file_name` is the basename, so the token that has to go is the whole
    // `dc/dc_boot.bin` the packager wrote.
    expect(describedAs("dc_boot.bin", "dc/dc_boot.bin (Dreamcast BIOS)")).toBe("(Dreamcast BIOS)");
  });

  it("sees a name that has a space in it", () => {
    // The token half cannot: this name is two tokens. It is the anchored
    // startsWith half that reaches it, and one of the corpus's entries is
    // spelled this way.
    expect(describedAs("7800 BIOS (U).rom", "7800 BIOS (U).rom (7800 BIOS)")).toBe("(7800 BIOS)");
  });

  it("prints a description whole when its first token names something else", () => {
    // A folder the file sits in, not the file — so there is nothing to take
    // out and the sentence is the packager's whole.
    expect(describedAs("hash.dat", "'Databases' folder")).toBe("'Databases' folder");
  });

  it("ignores quotes around a token that does name the file", () => {
    // The corpus's one quoted entry, read as a FILE row: what survives the
    // rule is the bare word, which is why `declared_kind` is guarded on at all
    // rather than this being left to say something on a folder row.
    expect(describedAs("bios", "'pcsx2/bios' folder")).toBe("folder");
  });

  it("shows none at all on a declared folder", () => {
    expect(describedAs("bios", "'pcsx2/bios' folder", "directory")).toBeNull();
  });

  it("shows none where the description is empty or only spaces", () => {
    expect(describedAs("dc_boot.bin", "")).toBeNull();
    expect(describedAs("dc_boot.bin", "   ")).toBeNull();
  });

  it("shows none of a packaged card's prose", () => {
    // The same field, a different kind of writing: atlas explaining the
    // requirement in whole sentences where a `.info` writes a label. It is the
    // DECLARATION that says which, never the file or the emulator behind it —
    // this row is DuckStation's, and its name is a PlayStation image like the
    // `read` rows above.
    expect(
      biosFileDescription({
        file_name: "scph1001.bin",
        description:
          "a PlayStation BIOS image — the console runs it before any disc, and DuckStation starts nothing " +
          "without one — found by the search, not named by any setting",
        declaration: "packaged",
      }),
    ).toBeNull();
  });

  it("shows none for a row that states no declaration", () => {
    // A file no emulator declared: its description is the file name, which the
    // rules above would take out anyway — so this pins the gate, not the
    // outcome. A payload from before the field existed lands here too.
    expect(biosFileDescription({ file_name: "scph5500.bin", description: "scph5500.bin (PS1 JP BIOS)" })).toBeNull();
  });
});
