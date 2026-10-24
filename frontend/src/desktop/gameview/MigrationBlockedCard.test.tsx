import "@testing-library/jest-dom/vitest";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MigrationBlockedCard } from "./MigrationBlockedCard";

describe("desktop MigrationBlockedCard", () => {
  it("names Big Picture as where the Tender menu is", () => {
    render(<MigrationBlockedCard />);

    expect(screen.getByText("RetroDECK Migration Required")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Open the Tender menu (QAM) in Big Picture to migrate files or dismiss the migration before playing.",
      ),
    ).toBeInTheDocument();
  });

  it("draws its left edge and its sign in the amber of Big Picture's warning cards", () => {
    const { container } = render(<MigrationBlockedCard />);

    const card = container.firstElementChild;
    expect(card).toHaveStyle({ borderLeft: "4px solid #ffaa00" });
    expect(card?.querySelector("svg")).toHaveStyle({ color: "#ffaa00" });
  });
});
