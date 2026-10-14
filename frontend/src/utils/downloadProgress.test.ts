import { describe, it, expect } from "vitest";
import {
  formatProgress,
  getDownloadFillGradient,
  getDownloadPulseColor,
  getDownloadBaseBackground,
} from "./downloadProgress";

// The expected strings are what CustomPlayButton rendered before these helpers
// left it, written out rather than rebuilt from the module's own stops.
describe("downloadProgress", () => {
  describe("formatProgress", () => {
    it("formats bytes (< 1024)", () => {
      expect(formatProgress(500, 800)).toBe("500 / 800 B");
    });

    it("formats kilobytes (< 1MB)", () => {
      expect(formatProgress(512 * 1024, 768 * 1024)).toBe("512.0 / 768.0 KB");
    });

    it("formats megabytes (< 1GB)", () => {
      expect(formatProgress(25.5 * 1024 * 1024, 100 * 1024 * 1024)).toBe("25.5 / 100.0 MB");
    });

    it("formats gigabytes (>= 1GB)", () => {
      expect(formatProgress(1.25 * 1024 * 1024 * 1024, 2.5 * 1024 * 1024 * 1024)).toBe("1.25 / 2.50 GB");
    });
  });

  describe("getDownloadFillGradient", () => {
    it("is solid green while extracting", () => {
      expect(getDownloadFillGradient(0.5, { extracting: true, downloading: true })).toBe(
        "linear-gradient(to right, rgb(80,200,47), rgb(24,177,78))",
      );
    });

    it("turns from blue to green as the download progresses", () => {
      expect(getDownloadFillGradient(0, { extracting: false, downloading: true })).toBe(
        "linear-gradient(to right, rgb(26, 159, 255), rgb(0, 120, 212))",
      );
      expect(getDownloadFillGradient(0.5, { extracting: false, downloading: true })).toBe(
        "linear-gradient(to right, rgb(53, 180, 151), rgb(12, 149, 145))",
      );
      expect(getDownloadFillGradient(1, { extracting: false, downloading: true })).toBe(
        "linear-gradient(to right, rgb(80, 200, 47), rgb(24, 177, 78))",
      );
    });

    it("is the download button's blue when nothing is downloading", () => {
      expect(getDownloadFillGradient(0.5, { extracting: false, downloading: false })).toBe(
        "linear-gradient(to right, #1a9fff, #0078d4)",
      );
    });
  });

  describe("getDownloadPulseColor", () => {
    it("is amber when paused", () => {
      expect(getDownloadPulseColor(0.5, { paused: true, extracting: false, downloading: true })).toBe(
        "rgba(212,167,44,0.7)",
      );
    });

    it("is green when extracting", () => {
      expect(getDownloadPulseColor(0.5, { paused: false, extracting: true, downloading: true })).toBe(
        "rgb(80, 200, 47)",
      );
    });

    it("follows the fill's left stop when downloading", () => {
      expect(getDownloadPulseColor(0.5, { paused: false, extracting: false, downloading: true })).toBe(
        "rgb(53, 180, 151)",
      );
    });

    it("is translucent blue when idle", () => {
      expect(getDownloadPulseColor(0, { paused: false, extracting: false, downloading: false })).toBe(
        "rgba(26,159,255,0.7)",
      );
    });
  });

  describe("getDownloadBaseBackground", () => {
    it("is muted slate when offline", () => {
      expect(getDownloadBaseBackground(0.5, { isOffline: true, extracting: false, downloading: true })).toBe(
        "linear-gradient(to right, #6b7b8b, #5a6a7a)",
      );
    });

    it("is dark green when extracting", () => {
      expect(getDownloadBaseBackground(0.5, { isOffline: false, extracting: true, downloading: true })).toBe(
        "linear-gradient(to right, #1a4d1a, #0f3320)",
      );
    });

    it("darkens with the fill when downloading", () => {
      const downloading = { isOffline: false, extracting: false, downloading: true };
      expect(getDownloadBaseBackground(0, downloading)).toBe(
        "linear-gradient(to right, rgb(10, 50, 90), rgb(5, 35, 65))",
      );
      expect(getDownloadBaseBackground(0.5, downloading)).toBe(
        "linear-gradient(to right, rgb(8, 43, 78), rgb(5, 43, 48))",
      );
      expect(getDownloadBaseBackground(1, downloading)).toBe(
        "linear-gradient(to right, rgb(5, 35, 65), rgb(5, 50, 30))",
      );
    });

    it("is the download button's blue when idle", () => {
      expect(getDownloadBaseBackground(0, { isOffline: false, extracting: false, downloading: false })).toBe(
        "linear-gradient(to right, #1a9fff, #0078d4)",
      );
    });
  });
});
