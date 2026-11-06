import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { useOutsideClick } from "./useOutsideClick";

describe("useOutsideClick", () => {
  it("does not call callback when inactive", () => {
    const onOutside = vi.fn();
    const div = document.createElement("div");
    document.body.appendChild(div);

    renderHook(() => {
      const ref = useRef<HTMLDivElement>(div);
      useOutsideClick(ref, onOutside, false);
    });

    const outsideDiv = document.createElement("div");
    document.body.appendChild(outsideDiv);
    outsideDiv.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));

    expect(onOutside).not.toHaveBeenCalled();
    div.remove();
    outsideDiv.remove();
  });

  it("calls callback when mousedown occurs outside element", () => {
    const onOutside = vi.fn();
    const div = document.createElement("div");
    document.body.appendChild(div);

    renderHook(() => {
      const ref = useRef<HTMLDivElement>(div);
      useOutsideClick(ref, onOutside, true);
    });

    const outsideDiv = document.createElement("div");
    document.body.appendChild(outsideDiv);
    outsideDiv.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));

    expect(onOutside).toHaveBeenCalledTimes(1);
    div.remove();
    outsideDiv.remove();
  });

  it("does not call callback when mousedown occurs inside element", () => {
    const onOutside = vi.fn();
    const div = document.createElement("div");
    const child = document.createElement("span");
    div.appendChild(child);
    document.body.appendChild(div);

    renderHook(() => {
      const ref = useRef<HTMLDivElement>(div);
      useOutsideClick(ref, onOutside, true);
    });

    child.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));

    expect(onOutside).not.toHaveBeenCalled();
    div.remove();
  });

  it("listens on the element's own document, not the one the code runs in", () => {
    const onOutside = vi.fn();
    const otherDoc = document.implementation.createHTMLDocument("desktop window");
    const div = otherDoc.createElement("div");
    const outside = otherDoc.createElement("div");
    otherDoc.body.append(div, outside);

    renderHook(() => {
      const ref = useRef<HTMLDivElement>(div);
      useOutsideClick(ref, onOutside, true);
    });

    document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    expect(onOutside).not.toHaveBeenCalled();

    outside.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    expect(onOutside).toHaveBeenCalledTimes(1);
  });

  it("listens nowhere while the ref holds no element", () => {
    const onOutside = vi.fn();
    const addEventListenerSpy = vi.spyOn(document, "addEventListener");

    renderHook(() => {
      const ref = useRef<HTMLDivElement>(null);
      useOutsideClick(ref, onOutside, true);
    });

    expect(addEventListenerSpy).not.toHaveBeenCalledWith("mousedown", expect.any(Function));
    addEventListenerSpy.mockRestore();
  });

  it("stops listening when it turns inactive and when it unmounts", () => {
    const onOutside = vi.fn();
    const otherDoc = document.implementation.createHTMLDocument("desktop window");
    const div = otherDoc.createElement("div");
    const outside = otherDoc.createElement("div");
    otherDoc.body.append(div, outside);
    const press = () => outside.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));

    const { rerender, unmount } = renderHook(
      ({ active }: { active: boolean }) => {
        const ref = useRef<HTMLDivElement>(div);
        useOutsideClick(ref, onOutside, active);
      },
      { initialProps: { active: true } },
    );
    rerender({ active: false });
    press();
    expect(onOutside).not.toHaveBeenCalled();

    rerender({ active: true });
    unmount();
    press();
    expect(onOutside).not.toHaveBeenCalled();
  });
});
