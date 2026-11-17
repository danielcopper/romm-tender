import { describe, it, expect } from "vitest";
import { HostTransportError } from "../api/host";
import { transportFailureSentence } from "./transportFailureWording";

const LEAD = "Failed to refresh artwork";

describe("transportFailureSentence (#2776 D9)", () => {
  it("names a lost connection as the cause", () => {
    expect(transportFailureSentence(LEAD, new HostTransportError("connection_lost", "socket closed"))).toBe(
      "Failed to refresh artwork: the connection to Tender's backend was lost.",
    );
  });

  it("answers a stranded panel with the panel's own sentence", () => {
    const stranded = new HostTransportError("stranded_panel", "Tender was restarted — restart Steam to use it again.");

    expect(transportFailureSentence(LEAD, stranded)).toBe("Tender was restarted — restart Steam to use it again.");
  });

  it.each(["backend_exception", "payload_too_large", "method_unknown", "malformed_message"])(
    "says only what failed for %s, naming no cause",
    (reason) => {
      expect(transportFailureSentence(LEAD, new HostTransportError(reason, "detail"))).toBe(LEAD);
    },
  );

  it("is null for an error that is no transport failure", () => {
    expect(transportFailureSentence(LEAD, new Error("boom"))).toBeNull();
  });
});
