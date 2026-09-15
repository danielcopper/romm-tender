import { describe, it, expect, afterEach } from "vitest";
import { isRomMAppId, registerRomMAppId, unregisterRomMAppId, rommAppIdCount } from "./rommAppIds";

// The registry is module-level state shared by every importer, which is the
// point of it — so each case unregisters what it added rather than relying on a
// fresh module per test. The set itself is private, so there is nothing to
// clear through; that is the property under test as much as any case below.
const REGISTERED = [4242, 9001];
afterEach(() => REGISTERED.forEach(unregisterRomMAppId));

describe("rommAppIds", () => {
  it("does not claim an appId nobody registered", () => {
    expect(isRomMAppId(4242)).toBe(false);
  });

  it("claims an appId once it is registered", () => {
    registerRomMAppId(4242);
    expect(isRomMAppId(4242)).toBe(true);
  });

  it("stops claiming an appId once it is unregistered", () => {
    registerRomMAppId(4242);
    unregisterRomMAppId(4242);
    expect(isRomMAppId(4242)).toBe(false);
  });

  it("unregisters only the appId it is given", () => {
    registerRomMAppId(4242);
    registerRomMAppId(9001);
    unregisterRomMAppId(4242);
    expect(isRomMAppId(9001)).toBe(true);
  });

  it("registers an appId once, so the count sees each shortcut one time", () => {
    registerRomMAppId(4242);
    registerRomMAppId(4242);
    expect(rommAppIdCount()).toBe(1);
  });

  it("ignores an unregister for an appId it never held", () => {
    unregisterRomMAppId(4242);
    expect(rommAppIdCount()).toBe(0);
  });

  it("counts every appId registered, which is what the debug line reads", () => {
    expect(rommAppIdCount()).toBe(0);
    registerRomMAppId(4242);
    registerRomMAppId(9001);
    expect(rommAppIdCount()).toBe(2);
    unregisterRomMAppId(4242);
    expect(rommAppIdCount()).toBe(1);
  });
});
