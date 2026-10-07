import "@testing-library/jest-dom/vitest";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { WarningCard } from "./WarningCard";

describe("WarningCard", () => {
  it("renders title and message", () => {
    render(<WarningCard title="Heads up" message="Something happened" />);
    expect(screen.getByText("Heads up")).toBeInTheDocument();
    expect(screen.getByText("Something happened")).toBeInTheDocument();
  });

  it("renders a title alone without an empty message line", () => {
    const { container } = render(<WarningCard title="Only a title" />);
    const root = container.firstChild as HTMLElement;
    expect(screen.getByText("Only a title")).toBeInTheDocument();
    expect(root.children).toHaveLength(2);
  });

  it("draws the warning sign by default", () => {
    const { container } = render(<WarningCard title="t" />);
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("leaves the warning sign out when showIcon is false", () => {
    const { container } = render(<WarningCard title="Only a fact" showIcon={false} />);
    const root = container.firstChild as HTMLElement;
    expect(container.querySelector("svg")).toBeNull();
    expect(root.children).toHaveLength(1);
    expect(screen.getByText("Only a fact")).toBeInTheDocument();
  });

  it("applies compact padding when compact=true", () => {
    const { container } = render(<WarningCard title="t" message="m" compact />);
    const root = container.firstChild as HTMLElement;
    expect(root).toHaveStyle({ padding: "24px 16px" });
  });

  it("draws a minor card in smaller type with less padding than a compact one", () => {
    const { container } = render(<WarningCard title="A fact" compact minor />);
    const root = container.firstChild as HTMLElement;
    expect(root).toHaveStyle({ padding: "8px 12px" });
    expect(screen.getByText("A fact")).toHaveStyle({ fontSize: "12px" });
  });

  it("draws a minor card's warning sign and message smaller than a compact one's", () => {
    const { container } = render(<WarningCard title="A fact" message="Why it matters" minor />);
    expect(container.querySelector("svg")).toHaveStyle({ fontSize: "16px" });
    expect(screen.getByText("Why it matters")).toHaveStyle({ fontSize: "11px" });
  });

  it("uses spacious padding by default", () => {
    const { container } = render(<WarningCard title="t" message="m" />);
    const root = container.firstChild as HTMLElement;
    expect(root).toHaveStyle({ padding: "40px 32px" });
  });
});
