import { describe, it, expect, beforeEach, vi } from "vitest";
import { render } from "@testing-library/react";
import { createElement, type ComponentProps } from "react";
import { VersionErrorCard } from "./VersionErrorCard";
import type { WarningCard } from "./WarningCard";

// Capture the props passed to WarningCard so the FC tests can assert the
// delegation contract without rendering WarningCard's own DOM (covered in
// WarningCard.test.tsx).
type CapturedWarningCardProps = ComponentProps<typeof WarningCard>;
const capturedWarningCard: CapturedWarningCardProps[] = [];

vi.mock("./WarningCard", () => ({
  WarningCard: (props: CapturedWarningCardProps) => {
    capturedWarningCard.push(props);
    return createElement("div", { "data-testid": "warning-card" });
  },
}));

describe("VersionErrorCard component", () => {
  beforeEach(() => {
    capturedWarningCard.length = 0;
  });

  it("delegates to WarningCard with the version-error title + message (default compact=false)", () => {
    const { queryByTestId } = render(<VersionErrorCard message="RomM 4.7.0 too old" />);
    expect(queryByTestId("warning-card")).not.toBeNull();
    expect(capturedWarningCard).toHaveLength(1);
    expect(capturedWarningCard[0]).toEqual({
      title: "RomM Server Update Required",
      message: "RomM 4.7.0 too old",
      compact: false,
    });
  });

  it("forwards compact=true", () => {
    render(<VersionErrorCard message="m" compact />);
    expect(capturedWarningCard[0]?.compact).toBe(true);
  });
});
