import type { FC } from "react";
import { BiosTab, useBiosTab, type BiosTabProps, type BiosTabStateInput } from "../bigpicture/BiosTab";

type BiosTabWithStateProps = Omit<BiosTabProps, "tabState"> & BiosTabStateInput;

/** The BIOS pane with the state the panel holds for it, for a test that renders the pane alone. */
export const BiosTabWithState: FC<BiosTabWithStateProps> = (props) => {
  const tabState = useBiosTab(props);
  return <BiosTab {...props} tabState={tabState} />;
};
