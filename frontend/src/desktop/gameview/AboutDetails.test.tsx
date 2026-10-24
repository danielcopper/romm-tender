import "@testing-library/jest-dom/vitest";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AboutDetails } from "./AboutDetails";
import type { RomMetadata } from "../../types";

const METADATA: RomMetadata = {
  summary: "A thrilling adventure across the kingdom.",
  genres: ["RPG", "Action"],
  companies: ["Camelot Software Planning"],
  first_release_date: null,
  average_rating: 80.4,
  game_modes: ["Single player", "Split screen"],
  player_count: "1-4",
  cached_at: 1000,
};

const labels = (container: HTMLElement) =>
  [...container.querySelectorAll("span")].filter((s) => s.style.minWidth === "90px").map((s) => s.textContent);

describe("AboutDetails", () => {
  it("draws the title, the summary, and the metadata rows under their labels in their order", () => {
    const { container } = render(
      <AboutDetails title="Golden Sun" platformName="Game Boy Advance" metadata={METADATA} covers={[]} />,
    );

    expect(screen.getByRole("heading", { name: "Golden Sun" })).toBeInTheDocument();
    expect(screen.getByText("A thrilling adventure across the kingdom.")).toBeInTheDocument();
    expect(labels(container)).toEqual(["Platform", "Companies", "Genres", "Game Modes", "Players", "Metadata rating"]);
    expect(screen.getByText("Single player, Split screen")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
  });

  it("draws a list value as one tag per entry", () => {
    render(<AboutDetails title="Golden Sun" platformName="GBA" metadata={METADATA} covers={[]} />);

    expect(screen.getByText("RPG")).toBeInTheDocument();
    expect(screen.getByText("Action")).toBeInTheDocument();
  });

  it("draws the platform alone where there is no record, and no heading for no title", () => {
    const { container } = render(<AboutDetails title="" platformName="SNES" metadata={null} covers={[]} />);

    expect(labels(container)).toEqual(["Platform"]);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  });

  it("shows the first cover, the next where it fails, and the platform's name once none loads", () => {
    render(
      <AboutDetails
        title="Golden Sun"
        platformName="GBA"
        metadata={null}
        covers={["https://steamloopback.host/customimages/1p.jpg", "https://steamloopback.host/customimages/1p.png"]}
      />,
    );

    const img = screen.getByRole("img", { name: "Golden Sun" });
    expect(img).toHaveAttribute("src", "https://steamloopback.host/customimages/1p.jpg");
    fireEvent.error(img);
    expect(screen.getByRole("img", { name: "Golden Sun" })).toHaveAttribute(
      "src",
      "https://steamloopback.host/customimages/1p.png",
    );
    fireEvent.error(screen.getByRole("img", { name: "Golden Sun" }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getAllByText("GBA")).toHaveLength(2);
  });

  it("tries a new list of covers from its first", () => {
    const { rerender } = render(
      <AboutDetails
        title="Golden Sun"
        platformName="GBA"
        metadata={null}
        covers={["https://a/1.jpg", "https://a/1.png"]}
      />,
    );
    fireEvent.error(screen.getByRole("img"));

    rerender(
      <AboutDetails
        title="Golden Sun"
        platformName="GBA"
        metadata={null}
        covers={["https://a/2.jpg", "https://a/2.png"]}
      />,
    );

    expect(screen.getByRole("img")).toHaveAttribute("src", "https://a/2.jpg");
  });
});
