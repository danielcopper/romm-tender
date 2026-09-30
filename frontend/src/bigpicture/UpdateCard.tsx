import { CSSProperties, FC, ReactNode } from "react";
import { PanelSectionRow, DialogButton, Field, Focusable } from "@decky/ui";

interface CardText {
  testId: string;
  color: string;
  wash: string;
  title: ReactNode;
  /** The line under the title; a card without one has none. */
  detail?: ReactNode;
}

/** The coloured frame an update card is drawn in; Settings › Updates states a failure in the same one. */
export const cardFrame = (color: string, wash: string): CSSProperties => ({
  padding: "8px 12px",
  backgroundColor: wash,
  borderLeft: `3px solid ${color}`,
  borderRadius: "4px",
  fontSize: "12px",
});

/** The coloured card every update notice on Main opens with: a title and, where there is one, a line under it. */
export const UpdateCardBody: FC<CardText> = ({ testId, color, wash, title, detail }) => (
  <PanelSectionRow>
    <Focusable onActivate={() => {}}>
      <div data-testid={testId} style={cardFrame(color, wash)}>
        <div style={{ fontWeight: "bold", color, ...(detail === undefined ? {} : { marginBottom: "4px" }) }}>
          {title}
        </div>
        {detail !== undefined && <div style={{ color: "rgba(255, 255, 255, 0.7)" }}>{detail}</div>}
      </div>
    </Focusable>
  </PanelSectionRow>
);

/**
 * The shape the update notices with a home share: the card, then **Open
 * Updates** and **Dismiss** side by side. They differ only in what they say
 * and in their colour.
 */
export const UpdateCard: FC<CardText & { onOpenUpdates: () => void; onDismiss: () => void }> = ({
  onOpenUpdates,
  onDismiss,
  ...text
}) => (
  <>
    <UpdateCardBody {...text} />
    <PanelSectionRow>
      <Field bottomSeparator="none" childrenLayout="below" childrenContainerWidth="max">
        <Focusable flow-children="horizontal" style={{ display: "flex", gap: "8px" }}>
          <DialogButton style={{ flex: 1, minWidth: 0, padding: "8px 0" }} onClick={onOpenUpdates}>
            Open Updates
          </DialogButton>
          <DialogButton style={{ flex: 1, minWidth: 0, padding: "8px 0" }} onClick={onDismiss}>
            Dismiss
          </DialogButton>
        </Focusable>
      </Field>
    </PanelSectionRow>
  </>
);
