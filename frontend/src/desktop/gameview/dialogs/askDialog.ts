import type { ReactNode } from "react";

/**
 * Ask the user one question in a desktop dialog: show what `render` draws and
 * settle with the value the dialog hands to `resolve`, or with `dismissed`
 * when the dialog is left without an answer or the question is taken away.
 */
export type AskDialog = <T>(dismissed: T, render: (resolve: (value: T) => void) => ReactNode) => Promise<T>;
