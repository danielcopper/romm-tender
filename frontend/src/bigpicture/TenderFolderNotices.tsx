import { FC, Fragment } from "react";
import { DialogButton, Field, Focusable, PanelSectionRow } from "@decky/ui";
import { AMBER, AMBER_WASH } from "./layout/pane";
import { UpdateCardBody } from "./UpdateCard";
import { WarningCard } from "./WarningCard";
import type { TenderFolderView } from "./useTenderFolder";
import {
  CHOOSE_ANOTHER_PLACE,
  ELSEWHERE_TITLE,
  FORGET_RECORDED,
  NOTHING_REMOVED,
  UNREACHABLE_TITLE,
  USE_THAT_FOLDER,
  elsewhereLine,
  folderConditionLine,
} from "../utils/tenderFolderWording";

const BUTTON = { flex: 1, minWidth: 0, padding: "8px 0" } as const;

const Detail: FC<{ line: string }> = ({ line }) => (
  <>
    <div>{line}</div>
    <div>{NOTHING_REMOVED}</div>
  </>
);

const Buttons: FC<{ first: string; onFirst: () => void; onForget: () => void; busy: boolean }> = ({
  first,
  onFirst,
  onForget,
  busy,
}) => (
  <PanelSectionRow>
    <Field bottomSeparator="none" childrenLayout="below" childrenContainerWidth="max">
      <Focusable flow-children="horizontal" style={{ display: "flex", gap: "8px" }}>
        <DialogButton style={BUTTON} disabled={busy} onClick={onFirst}>
          {first}
        </DialogButton>
        <DialogButton style={BUTTON} disabled={busy} onClick={onForget}>
          {FORGET_RECORDED}
        </DialogButton>
      </Focusable>
    </Field>
  </PanelSectionRow>
);

/**
 * Main's notices about Tender's folder: one while the folder something is
 * recorded under cannot be reached, and one for each other folder something is
 * recorded under — what a reset of the settings leaves behind. Neither can be
 * dismissed; each goes away once its way out is taken. With nothing recorded
 * under the folder, neither shows, whatever state the folder is in.
 */
export const TenderFolderNotices: FC<{ folder: TenderFolderView; onChooseAnotherPlace: () => void }> = ({
  folder,
  onChooseAnotherPlace,
}) => {
  const { state, busy, refusal } = folder;
  if (!state) return null;
  return (
    <>
      {state.unreachable !== null && (
        <>
          <UpdateCardBody
            testId="tender-folder-unreachable"
            color={AMBER}
            wash={AMBER_WASH}
            title={UNREACHABLE_TITLE}
            detail={<Detail line={folderConditionLine(state.folder, state.unreachable)} />}
          />
          <Buttons
            first={CHOOSE_ANOTHER_PLACE}
            onFirst={onChooseAnotherPlace}
            onForget={() => folder.forget(state.folder)}
            busy={busy}
          />
        </>
      )}
      {state.elsewhere.map((other) => (
        <Fragment key={other}>
          <UpdateCardBody
            testId={`tender-folder-elsewhere-${other}`}
            color={AMBER}
            wash={AMBER_WASH}
            title={ELSEWHERE_TITLE}
            detail={<Detail line={elsewhereLine(state.folder, other)} />}
          />
          <Buttons
            first={USE_THAT_FOLDER}
            onFirst={() => folder.switchToRecorded(other)}
            onForget={() => folder.forget(other)}
            busy={busy}
          />
        </Fragment>
      ))}
      {refusal !== null && (state.unreachable !== null || state.elsewhere.length > 0) && (
        <PanelSectionRow>
          <Focusable onActivate={() => {}}>
            <WarningCard title={refusal} compact />
          </Focusable>
        </PanelSectionRow>
      )}
    </>
  );
};
