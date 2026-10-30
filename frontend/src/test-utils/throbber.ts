/**
 * Where a wait's throbber is looked for: the line that says the wait.
 *
 * The throbber carries no text, so the element holding a waiting line reads
 * exactly the line's words whether the throbber stands beside them or not.
 */

/** The innermost element whose whole text is *text*, or `undefined` where none is. */
export function lineWithText(container: ParentNode, text: string): HTMLElement | undefined {
  const holding = [...container.querySelectorAll<HTMLElement>("*")].filter((el) => el.textContent === text);
  return holding.find((el) => ![...el.children].some((child) => child.textContent === text));
}

/** The throbber inside *el*, or `null` where it stands without one. */
export function throbberIn(el: Element | null | undefined): Element | null {
  return el?.querySelector('[data-testid="spinner"]') ?? null;
}
