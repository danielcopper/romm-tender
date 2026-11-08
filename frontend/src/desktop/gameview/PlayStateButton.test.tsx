import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PlayStateButton } from "./PlayStateButton";

const renderButton = (props: Partial<Parameters<typeof PlayStateButton>[0]> = {}) =>
  render(
    <PlayStateButton
      pressState={null}
      refusal={null}
      checkFailed={false}
      onPlay={vi.fn()}
      onUninstall={vi.fn()}
      {...props}
    />,
  );

describe("PlayStateButton", () => {
  it("presses PLAY, and opens the game's menu from the chevron with Uninstall in it", () => {
    const onPlay = vi.fn();
    const onUninstall = vi.fn();
    renderButton({ onPlay, onUninstall });

    fireEvent.click(screen.getByRole("button", { name: "PLAY" }));
    expect(onPlay).toHaveBeenCalledTimes(1);

    const toggle = screen.getByRole("button", { name: "Game Options" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveStyle({ cursor: "pointer" });
    expect(screen.queryByRole("menuitem", { name: "Uninstall" })).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("menuitem", { name: "Uninstall" }));
    expect(onUninstall).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menuitem", { name: "Uninstall" })).not.toBeInTheDocument();
  });

  it("closes the menu on a second press of the chevron, a click outside it, or a press of PLAY", () => {
    renderButton();
    const toggle = screen.getByRole("button", { name: "Game Options" });

    fireEvent.click(toggle);
    fireEvent.click(toggle);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "PLAY" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("raises its group above the page while the menu is open", () => {
    const { container } = renderButton();
    const group = container.querySelector(".tender-desktop-play-btn-group");
    expect(group).toHaveStyle({ zIndex: "20" });

    fireEvent.click(screen.getByRole("button", { name: "Game Options" }));
    expect(group).toHaveStyle({ zIndex: "1000" });
  });

  it.each([
    ["checking", "PREPARING..."],
    ["syncing", "SYNCING SAVES..."],
    ["launching", "LAUNCHING..."],
  ] as const)("reads %s's label, disables the main button, and offers no menu", (pressState, label) => {
    renderButton({ pressState });

    expect(screen.getByRole("button", { name: label })).toBeDisabled();
    const toggle = screen.getByRole("button", { name: "Game Options" });
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveStyle({ cursor: "default" });
    fireEvent.click(toggle);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("disables the chevron while a version switch is under way, and closes an open menu when one starts", () => {
    const { rerender } = renderButton();
    fireEvent.click(screen.getByRole("button", { name: "Game Options" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();

    rerender(
      <PlayStateButton
        pressState={null}
        refusal={null}
        checkFailed={false}
        switching
        onPlay={vi.fn()}
        onUninstall={vi.fn()}
      />,
    );

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Game Options" });
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveStyle({ cursor: "default" });
    expect(toggle).toHaveAttribute("title", "Game Options");
  });

  it("disables the chevron, saying why, while an uninstall of another version runs", () => {
    renderButton({ uninstallUnderWay: true });

    const toggle = screen.getByRole("button", { name: "Game Options" });
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveStyle({ cursor: "default" });
    expect(toggle).toHaveAttribute("title", "Another version of this game is being uninstalled");
    fireEvent.click(toggle);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it.each([
    ["a version switch", { switching: true }],
    ["a press", { pressState: "checking" as const }],
    ["another version's removal", { uninstallUnderWay: true }],
  ])("leaves a menu that %s closed shut once it ends", (_, refusing) => {
    const props = { pressState: null, refusal: null, checkFailed: false, onPlay: vi.fn(), onUninstall: vi.fn() };
    const { rerender } = render(<PlayStateButton {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Game Options" }));
    expect(screen.getByRole("menu")).toBeInTheDocument();

    rerender(<PlayStateButton {...props} {...refusing} />);
    rerender(<PlayStateButton {...props} />);

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Game Options" })).toHaveAttribute("aria-expanded", "false");
  });
});
