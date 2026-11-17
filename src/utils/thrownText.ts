/**
 * What was thrown, as text a log line can carry: told by its name and message,
 * read so that the reading cannot throw out of the catch it runs in. A value
 * whose name or message cannot be read is told by that alone.
 */
export function thrownText(thrown: unknown): string {
  try {
    if ((typeof thrown !== "object" && typeof thrown !== "function") || thrown === null) {
      return `${typeof thrown} ${String(thrown)}`;
    }
    const { name, message } = thrown as { name?: unknown; message?: unknown };
    const read = (part: unknown): string => (typeof part === "string" ? part : typeof part);
    return `${read(name)}: ${read(message)}`;
  } catch {
    return "unreadable";
  }
}
