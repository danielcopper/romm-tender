/**
 * The Library page: what of RomM is synced into Steam, in two L1/R1 tabs.
 *
 * **Platforms** is list and detail — the platform list on the left with its sync
 * toggle in the row, everything else about the focused platform on the right.
 * **Collections** is list and detail too — the kinds on the left, the selected
 * kind's collections as a table on the right.
 *
 * Both tabs' state lives here rather than in the tab components. Steam's tabbed
 * page renders only the active tab and keys it by tab id, so a tab component
 * owning its own reads would re-issue every one of them on each switch back.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Library.
 */

import { useState, FC } from "react";
import { WidePage, type WidePageTab } from "./layout/WidePage";
import { CollectionsTab } from "./library/CollectionsTab";
import { PlatformsTab } from "./library/PlatformsTab";
import { useCollectionsPage } from "./library/useCollectionsPage";
import { usePlatformsPage } from "./library/usePlatformsPage";

interface LibraryPageProps {
  onBack: () => void;
}

export const LibraryPage: FC<LibraryPageProps> = ({ onBack }) => {
  const [activeTab, setActiveTab] = useState<"platforms" | "collections">("platforms");
  const platformsState = usePlatformsPage();
  const collectionsState = useCollectionsPage();

  const tabs: WidePageTab[] = [
    { id: "platforms", title: "Platforms", content: <PlatformsTab state={platformsState} /> },
    { id: "collections", title: "Collections", content: <CollectionsTab state={collectionsState} /> },
  ];

  return (
    <WidePage
      title="Library"
      onBack={onBack}
      tabs={tabs}
      activeTab={activeTab}
      onShowTab={(tabId) => {
        if (tabId === "collections") {
          // Entered, not merely shown: the collections read waits for the tab,
          // and one that failed is asked again here.
          collectionsState.enter();
          setActiveTab("collections");
          return;
        }
        setActiveTab("platforms");
      }}
    />
  );
};
