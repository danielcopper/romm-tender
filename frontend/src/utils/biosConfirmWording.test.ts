import { describe, expect, it } from "vitest";
import { deleteBiosFileConfirmCopy, deletePlatformBiosConfirmCopy, replaceBiosConfirmCopy } from "./biosConfirmWording";

describe("BIOS confirmation copy builders", () => {
  it("names the file, place, and backup folder when confirming a replacement", () => {
    expect(replaceBiosConfirmCopy("scph5501.bin", "/bios/scph5501.bin", "/backups/bios")).toEqual({
      title: "Replace scph5501.bin?",
      description: "The file at /bios/scph5501.bin goes to /backups/bios, and Tender's copy is linked in its place.",
      confirmLabel: "Replace",
    });
    expect(replaceBiosConfirmCopy("scph5501.bin", "/bios/scph5501.bin", undefined).description).toContain(
      "the backup folder",
    );
  });

  it("states that user-placed files stay for per-file and per-platform deletes", () => {
    expect(deleteBiosFileConfirmCopy("scph5501.bin")).toEqual({
      title: "Delete scph5501.bin?",
      description: "Tender removes it from its firmware store and every link to it. Files you placed yourself stay.",
      confirmLabel: "Delete",
    });
    expect(deletePlatformBiosConfirmCopy("PlayStation")).toEqual({
      title: "Delete Tender's BIOS downloads for PlayStation?",
      description:
        "Tender removes every BIOS file it downloaded for PlayStation and every link to them. Files you placed yourself stay.",
      confirmLabel: "Delete",
    });
  });
});
