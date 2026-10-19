/**
 * Tender folder — where Tender keeps its own files: the current place and its
 * state, the internal default, each writable drive with its free space, and a
 * place typed by hand. Choosing switches the place and nothing else. Pure
 * renderer: the parent owns the folder's state and the changes.
 */

import { FC } from "react";
import { ButtonItem, Field, Focusable, PanelSection, PanelSectionRow, showModal } from "@decky/ui";
import type { TenderFolderView } from "../useTenderFolder";
import {
  ANOTHER_FOLDER,
  CURRENT_PLACE,
  INTERNAL_DEFAULT,
  TENDER_FOLDER_SECTION,
  driveFreeLine,
  driveRowLabel,
  folderConditionLine,
} from "../../utils/tenderFolderWording";
import { WarningCard } from "../WarningCard";
import { TextInputModal } from "./TextInputModal";

export const TenderFolderSection: FC<{ folder: TenderFolderView }> = ({ folder }) => {
  const { state, busy, refusal } = folder;
  const askTyped = () =>
    showModal(<TextInputModal label={ANOTHER_FOLDER} value={state?.place ?? ""} onSubmit={folder.chooseTyped} />);
  return (
    <PanelSection title={TENDER_FOLDER_SECTION}>
      {state && (
        <PanelSectionRow>
          <Field
            label={CURRENT_PLACE}
            description={state.condition === null ? state.folder : folderConditionLine(state.folder, state.condition)}
            focusable
          />
        </PanelSectionRow>
      )}
      {refusal !== null && (
        <PanelSectionRow>
          <Focusable onActivate={() => {}}>
            <WarningCard title={refusal} compact />
          </Focusable>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={folder.chooseDefault}>
          {INTERNAL_DEFAULT}
        </ButtonItem>
      </PanelSectionRow>
      {state?.drives.map((drive) => (
        <PanelSectionRow key={drive.mount_point}>
          <ButtonItem
            layout="below"
            description={driveFreeLine(drive)}
            disabled={busy}
            onClick={() => folder.chooseDrive(drive.mount_point)}
          >
            {driveRowLabel(drive)}
          </ButtonItem>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy} onClick={askTyped}>
          {ANOTHER_FOLDER}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
};
