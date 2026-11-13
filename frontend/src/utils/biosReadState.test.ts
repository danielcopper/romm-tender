import { describe, expect, it } from "vitest";
import type { BiosAnswer } from "../api/backend";
import { biosAnswerStale, biosFieldsFromCache, biosReadState } from "./biosReadState";

describe("biosReadState", () => {
  it("is pending while nothing is held and no read has failed", () => {
    expect(biosReadState(false, false)).toBe("pending");
  });

  it("is failed when a read failed with nothing held", () => {
    expect(biosReadState(false, true)).toBe("failed");
  });

  it("is answered whenever an answer is held, a failed re-read included", () => {
    expect(biosReadState(true, false)).toBe("answered");
    expect(biosReadState(true, true)).toBe("answered");
  });
});

describe("biosAnswerStale", () => {
  it("is set only for a held answer whose latest read failed", () => {
    expect(biosAnswerStale(true, true)).toBe(true);
    expect(biosAnswerStale(true, false)).toBe(false);
    expect(biosAnswerStale(false, true)).toBe(false);
  });
});

describe("biosFieldsFromCache", () => {
  it("keeps what an answer nothing could establish carries for buttons and emulator tabs", () => {
    const tabs = [{ emulator: "n64_libretro.so", label: "Mupen64", launching: true, requirements_met: null }];
    const fields = biosFieldsFromCache({
      bios_status: {
        needs_bios: false,
        bios_status_unknown: true,
        files: [],
        platform_slug: "n64",
        deletable_count: 2,
        server_offline: false,
        emulator_tabs: tabs,
      } as unknown as NonNullable<BiosAnswer["bios_status"]>,
      bios_level: "unknown",
      bios_label: "Unknown",
      bios_status_unknown: true,
    });

    expect(fields?.biosLevel).toBe("unknown");
    expect(fields?.biosStatus).toMatchObject({
      needs_bios: false,
      bios_status_unknown: true,
      files: [],
      deletable_count: 2,
      emulator_tabs: tabs,
    });
  });

  it("is no answer where bios_status_unknown comes without the unknown level", () => {
    expect(biosFieldsFromCache({ bios_status: null, bios_level: null, bios_status_unknown: true })).toBeNull();
  });

  it("returns null when neither bios_status nor bios_status_unknown is present", () => {
    expect(biosFieldsFromCache({ bios_status: null, bios_level: "missing" })).toBeNull();
  });

  it("threads bios_status and bios_level straight through for a known answer", () => {
    const status = {
      platform_slug: "psx",
      server_count: 2,
      local_count: 1,
      all_downloaded: false,
    };
    expect(biosFieldsFromCache({ bios_status: status, bios_level: "missing", bios_label: "1/2" })).toEqual({
      biosStatus: { needs_bios: true, ...status },
      biosLevel: "missing",
    });
  });
});
