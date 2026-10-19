import { describe, it, expect } from "vitest";
import {
  findSteamOverviewPanel,
  findSteamPlaySection,
  findSteamPlayButton,
  findSteamContentSections,
  findSteamStickyPlayBar,
  isPlayBarElement,
  isTenderElement,
  isRightControlsElement,
  findSteamPlayBarBadges,
  findSteamRightControls,
  findHeroWrapperFallback,
  findPlayBarAndContainer,
  locateOverviewPanel,
  locatePlayButton,
  TENDER_PLAY_BUTTON_ID,
  TENDER_SUBSTITUTE_ID,
} from "./elementSelectors";
import * as deckyUiInternals from "../../utils/deckyUiInternals";

describe("elementSelectors", () => {
  describe("isTenderElement", () => {
    it("keeps Tender's two host ids apart, and both under tender-", () => {
      expect(TENDER_SUBSTITUTE_ID).toMatch(/^tender-/);
      expect(TENDER_PLAY_BUTTON_ID).toMatch(/^tender-/);
      expect(TENDER_SUBSTITUTE_ID).not.toBe(TENDER_PLAY_BUTTON_ID);
    });

    it("identifies Tender substitute and play button by ID", () => {
      const el1 = document.createElement("div");
      el1.id = TENDER_PLAY_BUTTON_ID;
      expect(isTenderElement(el1)).toBe(true);

      const el2 = document.createElement("div");
      el2.id = TENDER_SUBSTITUTE_ID;
      expect(isTenderElement(el2)).toBe(true);
    });

    it("identifies descendants of Tender containers", () => {
      const parent = document.createElement("div");
      parent.id = TENDER_SUBSTITUTE_ID;
      const child = document.createElement("span");
      parent.appendChild(child);
      document.body.appendChild(parent);

      expect(isTenderElement(child)).toBe(true);
      parent.remove();
    });

    it("identifies elements with tender- class names", () => {
      const el = document.createElement("div");
      el.className = "tender-desktop-card";
      expect(isTenderElement(el)).toBe(true);
    });

    it("returns false for non-Tender elements", () => {
      const el = document.createElement("div");
      el.className = "AppDetailsOverviewPanel";
      expect(isTenderElement(el)).toBe(false);
    });
  });

  describe("isPlayBarElement with Fiber and classes", () => {
    it("recognizes element via fiber component name", () => {
      const el = document.createElement("div");
      (el as unknown as Record<string, unknown>)["__reactFiber$test"] = {
        type: { displayName: "PlayBar" },
      };
      expect(isPlayBarElement(el)).toBe(true);
    });

    it("recognizes element via substring class name", () => {
      const el = document.createElement("div");
      el.className = "header_InPage_2xK9";
      expect(isPlayBarElement(el)).toBe(true);
    });

    it("returns false for unrelated elements", () => {
      const el = document.createElement("div");
      el.className = "RightColumn";
      expect(isPlayBarElement(el)).toBe(false);
    });
  });

  describe("findSteamOverviewPanel", () => {
    it("finds panel using exact class from appDetailsClasses", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const panel = doc.createElement("div");
      panel.className = deckyUiInternals.appDetailsClasses?.AppDetailsOverviewPanel || "AppDetailsOverviewPanel";
      doc.body.appendChild(panel);

      expect(findSteamOverviewPanel(doc)).toBe(panel);
    });

    it("falls back to substring class match when webpack class is hashed", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const panel = doc.createElement("div");
      panel.className = "appdetailsoverview_AppDetailsOverviewPanel_3xK9 custom-other";
      doc.body.appendChild(panel);

      expect(findSteamOverviewPanel(doc)).toBe(panel);
    });

    it("finds panel via Fiber component name when classes are unhashed", () => {
      const doc = document.implementation.createHTMLDocument();
      const panel = doc.createElement("div");
      panel.className = "CustomPanelWrapper";
      (panel as unknown as Record<string, unknown>)["__reactFiber$123"] = {
        type: { displayName: "AppDetailsOverviewPanel" },
      };
      doc.body.appendChild(panel);

      expect(findSteamOverviewPanel(doc)).toBe(panel);
    });

    it("ignores our own substitute container even if classes match", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const sub = doc.createElement("div");
      sub.id = TENDER_SUBSTITUTE_ID;
      sub.className = "AppDetailsOverviewPanel";
      doc.body.appendChild(sub);

      expect(findSteamOverviewPanel(doc)).toBeNull();
    });
  });

  describe("findSteamPlayButton", () => {
    it("finds play button via PlayButtonContainer class", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const pbc = doc.createElement("div");
      pbc.className = deckyUiInternals.appActionButtonClasses?.PlayButtonContainer || "PlayButtonContainer";
      doc.body.appendChild(pbc);

      expect(findSteamPlayButton(doc)).toBe(pbc);
    });

    it("finds play button via PlayButton class", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const btn = doc.createElement("button");
      btn.className = deckyUiInternals.appActionButtonClasses?.PlayButton || "PlayButton";
      doc.body.appendChild(btn);

      expect(findSteamPlayButton(doc)).toBe(btn);
    });

    it("returns PlayButtonContainer parent if button is wrapped in one", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const container = doc.createElement("div");
      container.className = "custom_PlayButtonContainer_hash";
      const btn = doc.createElement("button");
      btn.className = "PlayButton";
      container.appendChild(btn);
      doc.body.appendChild(container);

      expect(findSteamPlayButton(doc)).toBe(container);
    });

    it("finds button with Fiber name", () => {
      const root = document.createElement("div");
      const btn = document.createElement("button");
      (btn as unknown as Record<string, unknown>)["__reactFiber$btn"] = {
        type: { displayName: "PlayButton" },
      };
      root.appendChild(btn);

      expect(findSteamPlayButton(root)).toBe(btn);
    });

    it("returns the container of a button found by its Fiber name, whatever the case of the container's class", () => {
      const root = document.createElement("div");
      const container = document.createElement("div");
      container.className = "Playbuttoncontainer_x";
      const btn = document.createElement("button");
      (btn as unknown as Record<string, unknown>)["__reactFiber$btn"] = {
        type: { displayName: "PlayButton" },
      };
      container.appendChild(btn);
      root.appendChild(container);

      expect(findSteamPlayButton(root)).toBe(container);
    });

    it("does not take a button by its label or its text, which read differently in every language", () => {
      const root = document.createElement("div");
      const labelled = document.createElement("button");
      labelled.setAttribute("aria-label", "Play Game");
      const worded = document.createElement("button");
      worded.textContent = "PLAY";
      root.append(labelled, worded);

      expect(findSteamPlayButton(root)).toBeNull();
    });

    it("returns null when no play button elements exist", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      expect(findSteamPlayButton(doc)).toBeNull();
    });

    it.each([
      ["class name", "PlayButton", undefined],
      ["Fiber name", "custom-button", "PlayButton"],
    ] as const)("names the %s rung that found it", (rung, className, fiberName) => {
      const root = document.createElement("div");
      const btn = document.createElement("button");
      btn.className = className;
      if (fiberName) {
        (btn as unknown as Record<string, unknown>)["__reactFiber$btn"] = { type: { displayName: fiberName } };
      }
      root.appendChild(btn);

      expect(locatePlayButton(root)).toEqual({ element: btn, rung });
    });
  });

  describe("locateOverviewPanel", () => {
    it.each([
      ["class name", "appdetails_AppDetailsOverviewPanel_x", undefined],
      ["Fiber name", "SomePanel", "AppDetailsOverviewPanel"],
    ] as const)("names the %s rung that found it", (rung, className, fiberName) => {
      const doc = document.implementation.createHTMLDocument("Test");
      const panel = doc.createElement("div");
      panel.className = className;
      if (fiberName) {
        (panel as unknown as Record<string, unknown>)["__reactFiber$p"] = { type: { displayName: fiberName } };
      }
      doc.body.appendChild(panel);

      expect(locateOverviewPanel(doc)).toEqual({ element: panel, rung });
    });

    it("names the structural rung for the right panel's first child", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const rightPanel = doc.createElement("div");
      rightPanel.className = "RightPanel";
      const firstChild = doc.createElement("div");
      rightPanel.appendChild(firstChild);
      doc.body.appendChild(rightPanel);

      expect(locateOverviewPanel(doc)).toEqual({ element: firstChild, rung: "structure" });
    });
  });

  describe("findPlayBarAndContainer's rung", () => {
    it("names the rung that recognised the play bar", () => {
      const panel = document.createElement("div");
      const playBar = document.createElement("div");
      playBar.className = "PlayBar";
      const playSection = document.createElement("div");
      playBar.appendChild(playSection);
      panel.append(playBar, document.createElement("div"));

      expect(findPlayBarAndContainer(panel, playSection).rung).toBe("class name");
    });

    it("names the structural rung when no rung recognised one", () => {
      const panel = document.createElement("div");
      const wrapper = document.createElement("div");
      const playSection = document.createElement("div");
      wrapper.appendChild(playSection);
      panel.append(wrapper, document.createElement("div"));

      expect(findPlayBarAndContainer(panel, playSection)).toEqual({
        playBarTop: wrapper,
        container: panel,
        rung: "structure",
      });
    });
  });

  describe("findSteamPlaySection", () => {
    it("finds play section using exact class from basicAppDetailsSectionStylerClasses", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const ps = doc.createElement("div");
      ps.className = deckyUiInternals.basicAppDetailsSectionStylerClasses?.PlaySection || "PlaySection";
      doc.body.appendChild(ps);

      expect(findSteamPlaySection(doc)).toBe(ps);
    });

    it("finds play section using PlayBar class from appDetailsClasses", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const pb = doc.createElement("div");
      pb.className = deckyUiInternals.appDetailsClasses?.PlayBar || "PlayBar";
      doc.body.appendChild(pb);

      expect(findSteamPlaySection(doc)).toBe(pb);
    });

    it("falls back to substring class match for PlaySection", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const ps = doc.createElement("div");
      ps.className = "custom_PlaySection_1234 other-class";
      doc.body.appendChild(ps);

      expect(findSteamPlaySection(doc)).toBe(ps);
    });

    it("locates play section containing a play button", () => {
      const root = document.createElement("div");
      const section = document.createElement("div");
      section.className = "PlaySection";
      const btn = document.createElement("button");
      (btn as unknown as Record<string, unknown>)["__reactFiber$btn"] = { type: { displayName: "PlayButton" } };
      section.appendChild(btn);
      root.appendChild(section);

      expect(findSteamPlaySection(root)).toBe(section);
    });

    it("falls back to play button ancestor when no section class matches", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const actionContainer = doc.createElement("div");
      actionContainer.className = "action-row";
      const btn = doc.createElement("button");
      btn.className = "AppActionButton PlayButton";
      actionContainer.appendChild(btn);
      doc.body.appendChild(actionContainer);

      expect(findSteamPlaySection(doc)).toBe(actionContainer);
    });
  });

  describe("findSteamContentSections", () => {
    it("returns overviewPanel itself when playSection is outside it", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playSection = doc.createElement("div");
      const overviewPanel = doc.createElement("div");
      doc.body.appendChild(playSection);
      doc.body.appendChild(overviewPanel);

      const sections = findSteamContentSections(overviewPanel, playSection);
      expect(sections).toEqual([overviewPanel]);
    });

    it("finds section list inside overviewPanel", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const overviewPanel = doc.createElement("div");
      const playSection = doc.createElement("div");
      playSection.className = "PlaySection";
      const sectionList = doc.createElement("div");
      sectionList.className =
        deckyUiInternals.basicAppDetailsSectionStylerClasses?.AppDetailSectionList || "AppDetailSectionList";
      overviewPanel.appendChild(playSection);
      overviewPanel.appendChild(sectionList);
      doc.body.appendChild(overviewPanel);

      const sections = findSteamContentSections(overviewPanel, playSection);
      expect(sections).toContain(sectionList);
    });

    it("returns siblings after play bar when inside overviewPanel", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const overviewPanel = doc.createElement("div");
      const playSection = doc.createElement("div");
      playSection.className = "PlaySection";
      const sibling1 = doc.createElement("div");
      sibling1.className = "other-content-1";
      const sibling2 = doc.createElement("div");
      sibling2.className = "other-content-2";
      overviewPanel.appendChild(playSection);
      overviewPanel.appendChild(sibling1);
      overviewPanel.appendChild(sibling2);
      doc.body.appendChild(overviewPanel);

      const sections = findSteamContentSections(overviewPanel, playSection);
      expect(sections).toEqual([sibling1, sibling2]);
    });

    it("correctly identifies inner container and hides following content siblings with hashed class names", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const overviewPanel = doc.createElement("div");
      overviewPanel.className = "AppDetailsOverviewPanel";
      const innerContainer = doc.createElement("div");
      innerContainer.className = "_27RcNu8aXKBpYkHcNNrt-X _2OOzYVWIHaKXm6_7sscT9i";
      const playBar = doc.createElement("div");
      playBar.className = "_3fLo166MlaNqP8r8tTyRz _1U7LKpx70kEsz3jJwAFOi-";
      const playButton = doc.createElement("button");
      playBar.appendChild(playButton);
      const shortcutNotice = doc.createElement("div");
      shortcutNotice.className = "_2K2bYzcKrhqLqDp0Jy6Ksz _2jPMy2QZr8bWi6yrk5ZzHA";
      const columnContainer = doc.createElement("div");
      columnContainer.className = "OhSdLYuggDtBcWjYP0j_9";

      innerContainer.appendChild(playBar);
      innerContainer.appendChild(shortcutNotice);
      innerContainer.appendChild(columnContainer);
      overviewPanel.appendChild(innerContainer);
      doc.body.appendChild(overviewPanel);

      const sections = findSteamContentSections(overviewPanel, playButton);
      expect(sections).toContain(shortcutNotice);
      expect(sections).toContain(columnContainer);
      expect(sections).not.toContain(playBar);
    });

    it("elevates playBarTop to in-page play bar container when playBar is wrapped inside it", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const overviewPanel = doc.createElement("div");
      overviewPanel.className = "AppDetailsOverviewPanel";
      const innerContainer = doc.createElement("div");
      innerContainer.className = "_27RcNu8aXKBpYkHcNNrt-X _2OOzYVWIHaKXm6_7sscT9i";

      const inPageContainer = doc.createElement("div");
      inPageContainer.className = "_3Yf8b2v5oOD8Wqsxu04ar _1U7LKpx70kEsz3jJwAFOi-";
      const playBar = doc.createElement("div");
      playBar.className = "_3fLo166MlaNqP8r8tTyRz _3DeO92O5aVkcdwEBCJDjWm";
      const playBtn = doc.createElement("button");
      playBar.appendChild(playBtn);
      const shadow = doc.createElement("div");
      shadow.className = "_2_86QNCjVvJTL3Qe6Xztx_";
      inPageContainer.appendChild(playBar);
      inPageContainer.appendChild(shadow);

      const columnContainer = doc.createElement("div");
      columnContainer.className = "OhSdLYuggDtBcWjYP0j_9";

      innerContainer.appendChild(inPageContainer);
      innerContainer.appendChild(columnContainer);
      overviewPanel.appendChild(innerContainer);
      doc.body.appendChild(overviewPanel);

      const sections = findSteamContentSections(overviewPanel, playBtn);
      expect(sections).toContain(columnContainer);
      expect(sections).not.toContain(inPageContainer);
      expect(sections).not.toContain(playBar);
      expect(sections).not.toContain(shadow);
    });
  });

  describe("isRightControlsElement and findSteamRightControls", () => {
    it("identifies right controls via Fiber component name", () => {
      const el = document.createElement("div");
      (el as unknown as Record<string, unknown>)["__reactFiber$rc"] = {
        type: { displayName: "RightControls" },
      };
      expect(isRightControlsElement(el)).toBe(true);
    });

    it("finds right controls in a play bar", () => {
      const root = document.createElement("div");
      const rc = document.createElement("div");
      rc.className = "RightControls";
      root.appendChild(rc);

      expect(findSteamRightControls(root)).toBe(rc);
    });

    it("identifies RightControls container", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      playBar.className = "PlayBar";

      const rightControls = doc.createElement("div");
      rightControls.className = "playsection_RightControls_hash";
      playBar.appendChild(rightControls);

      expect(findSteamRightControls(playBar)).toBe(rightControls);
    });

    it("identifies AppButtonsContainer and AppButtons", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      playBar.className = "PlayBar";

      const appButtons = doc.createElement("div");
      appButtons.className = "AppButtonsContainer AppButtons";
      playBar.appendChild(appButtons);

      expect(findSteamRightControls(playBar)).toBe(appButtons);
    });

    it("identifies parent container by controller config or favorite button", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      playBar.className = "PlayBar";

      const wrapper = doc.createElement("div");
      wrapper.className = "custom-actions-wrapper";
      const configBtn = doc.createElement("button");
      configBtn.className = "ControllerConfigButton";
      wrapper.appendChild(configBtn);
      playBar.appendChild(wrapper);

      expect(findSteamRightControls(playBar)).toBe(wrapper);
    });

    it("ignores Tender elements even if classes match", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      playBar.className = "PlayBar";

      const tenderEl = doc.createElement("div");
      tenderEl.id = TENDER_PLAY_BUTTON_ID;
      tenderEl.className = "RightControls";
      playBar.appendChild(tenderEl);

      expect(findSteamRightControls(playBar)).toBeNull();
    });

    it("returns null when no right-side controls exist", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      expect(findSteamRightControls(playBar)).toBeNull();
    });
  });

  describe("findSteamPlayBarBadges", () => {
    it("identifies StatusAndStats and GameStatsSection containers", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      playBar.className = "PlayBar";

      const statusAndStats = doc.createElement("div");
      statusAndStats.className = "playsection_StatusAndStats_hash";
      const statsSection = doc.createElement("div");
      statsSection.className = "GameStatsSection";

      statusAndStats.appendChild(statsSection);
      playBar.appendChild(statusAndStats);

      const badges = findSteamPlayBarBadges(playBar);
      expect(badges).toContain(statusAndStats);
      expect(badges).toContain(statsSection);
    });

    it("identifies individual stat items like LastPlayed, Playtime, CloudStatus, MiniAchievements", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      playBar.className = "PlayBar";

      const lastPlayed = doc.createElement("div");
      lastPlayed.className = "custom_LastPlayed_123";
      const playtime = doc.createElement("div");
      playtime.className = "custom_Playtime_456";
      const cloudStatus = doc.createElement("div");
      cloudStatus.className = "CloudStatusRow";
      const achievements = doc.createElement("div");
      achievements.className = "MiniAchievements";

      playBar.appendChild(lastPlayed);
      playBar.appendChild(playtime);
      playBar.appendChild(cloudStatus);
      playBar.appendChild(achievements);

      const badges = findSteamPlayBarBadges(playBar);
      expect(badges).toContain(lastPlayed);
      expect(badges).toContain(playtime);
      expect(badges).toContain(cloudStatus);
      expect(badges).toContain(achievements);
    });

    it("identifies a stat item by its PlayBarDetailLabel", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      playBar.className = "PlayBar";
      const statItem = doc.createElement("div");
      statItem.className = "game-stat-wrapper";
      const label = doc.createElement("div");
      label.className = "PlayBarDetailLabel";
      statItem.appendChild(label);
      playBar.appendChild(statItem);

      expect(findSteamPlayBarBadges(playBar)).toContain(statItem);
    });

    it("does not take a badge by its text, which reads differently in every language", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      playBar.className = "PlayBar";
      const statItem = doc.createElement("div");
      statItem.className = "custom-wrapper";
      const label = doc.createElement("div");
      label.textContent = "LAST PLAYED";
      statItem.appendChild(label);
      playBar.appendChild(statItem);

      expect(findSteamPlayBarBadges(playBar)).toEqual([]);
    });

    it("identifies GameStat badges while ignoring Tender elements", () => {
      const root = document.createElement("div");
      const stat = document.createElement("div");
      stat.className = "GameStat";
      stat.textContent = "10 hours";
      root.appendChild(stat);

      const badges = findSteamPlayBarBadges(root);
      expect(badges).toContain(stat);
    });

    it("ignores Tender elements and custom badges", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      playBar.className = "PlayBar";

      const tenderHost = doc.createElement("div");
      tenderHost.id = TENDER_PLAY_BUTTON_ID;
      const tenderBadge = doc.createElement("div");
      tenderBadge.className = "tender-desktop-badge-item tender-desktop-last-played";
      const tenderLabel = doc.createElement("div");
      tenderLabel.textContent = "LAST PLAYED";
      tenderBadge.appendChild(tenderLabel);
      tenderHost.appendChild(tenderBadge);
      playBar.appendChild(tenderHost);

      const badges = findSteamPlayBarBadges(playBar);
      expect(badges).toHaveLength(0);
      expect(isTenderElement(tenderHost)).toBe(true);
      expect(isTenderElement(tenderBadge)).toBe(true);
    });

    it("ignores RightControls and action buttons (gear, controller, heart)", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      playBar.className = "PlayBar";

      const rightControls = doc.createElement("div");
      rightControls.className = "RightControls AppButtonsContainer";
      const gearBtn = doc.createElement("button");
      gearBtn.className = "AppActionButton";
      rightControls.appendChild(gearBtn);
      playBar.appendChild(rightControls);

      expect(isRightControlsElement(rightControls)).toBe(true);
      const badges = findSteamPlayBarBadges(playBar);
      expect(badges).not.toContain(rightControls);
      expect(badges).not.toContain(gearBtn);
    });

    it("ignores nativePlayBtn when provided", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      const playBtn = doc.createElement("button");
      playBtn.className = "PlayButton AppActionButton";
      playBar.appendChild(playBtn);

      const badges = findSteamPlayBarBadges(playBar, playBtn);
      expect(badges).not.toContain(playBtn);
    });

    it("handles elements with null textContent without error", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const playBar = doc.createElement("div");
      const child = doc.createElement("div");
      Object.defineProperty(child, "textContent", { value: null });
      playBar.appendChild(child);

      expect(findSteamPlayBarBadges(playBar)).toEqual([]);
    });
  });

  describe("findSteamStickyPlayBar", () => {
    it("finds sticky play bar via appDetailsClasses.PlayBar across document", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const inPagePlayBar = doc.createElement("div");
      inPagePlayBar.className = "InPagePlayBar";
      doc.body.appendChild(inPagePlayBar);

      const stickyPlayBar = doc.createElement("div");
      stickyPlayBar.className = deckyUiInternals.appDetailsClasses?.PlayBar || "PlayBar";
      doc.body.appendChild(stickyPlayBar);

      expect(findSteamStickyPlayBar(doc, inPagePlayBar)).toBe(stickyPlayBar);
    });

    it("finds sticky play bar when situated outside overviewPanel as a sibling under main window split", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const splitRoot = doc.createElement("div");
      splitRoot.className = "MainWindowSplit";

      const stickyPlayBar = doc.createElement("div");
      stickyPlayBar.className = deckyUiInternals.appDetailsClasses?.PlayBar || "PlayBar";

      const scrollContainer = doc.createElement("div");
      scrollContainer.className = "ScrollContainer";
      const overviewPanel = doc.createElement("div");
      overviewPanel.className = "AppDetailsOverviewPanel";
      const inPagePlayBar = doc.createElement("div");
      inPagePlayBar.className = "InPage";

      overviewPanel.appendChild(inPagePlayBar);
      scrollContainer.appendChild(overviewPanel);
      splitRoot.appendChild(stickyPlayBar);
      splitRoot.appendChild(scrollContainer);
      doc.body.appendChild(splitRoot);

      expect(findSteamStickyPlayBar(doc, inPagePlayBar)).toBe(stickyPlayBar);
    });

    it("finds sticky play bar via playSectionClasses.StickyHeader and elevates to outer sticky container", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const inPagePlayBar = doc.createElement("div");
      inPagePlayBar.className = "InPagePlayBar";
      doc.body.appendChild(inPagePlayBar);

      const outerSticky = doc.createElement("div");
      outerSticky.className = "custom_PlayBar_sticky_wrapper";
      const innerSticky = doc.createElement("div");
      innerSticky.className = deckyUiInternals.playSectionClasses?.StickyHeader || "StickyHeader";
      outerSticky.appendChild(innerSticky);
      doc.body.appendChild(outerSticky);

      expect(findSteamStickyPlayBar(doc, inPagePlayBar)).toBe(outerSticky);
    });

    it("finds sticky play bar via appDetailsClasses.ShowPlayBar", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const inPagePlayBar = doc.createElement("div");
      inPagePlayBar.className = "InPagePlayBar";
      doc.body.appendChild(inPagePlayBar);

      const stickyPlayBar = doc.createElement("div");
      stickyPlayBar.className = "custom-sticky " + (deckyUiInternals.appDetailsClasses?.ShowPlayBar || "ShowPlayBar");
      doc.body.appendChild(stickyPlayBar);

      expect(findSteamStickyPlayBar(doc, inPagePlayBar)).toBe(stickyPlayBar);
    });

    it("ignores inPagePlayBar and elements inside inPagePlayBar", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const inPagePlayBar = doc.createElement("div");
      inPagePlayBar.className = deckyUiInternals.appDetailsClasses?.PlayBar || "PlayBar";
      const inner = doc.createElement("div");
      inner.className = deckyUiInternals.appDetailsClasses?.PlayBar || "PlayBar";
      inPagePlayBar.appendChild(inner);
      doc.body.appendChild(inPagePlayBar);

      expect(findSteamStickyPlayBar(doc, inPagePlayBar)).toBeNull();
    });

    it("ignores Tender elements", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const inPagePlayBar = doc.createElement("div");
      inPagePlayBar.className = "InPagePlayBar";
      doc.body.appendChild(inPagePlayBar);

      const tenderSticky = doc.createElement("div");
      tenderSticky.id = TENDER_PLAY_BUTTON_ID;
      tenderSticky.className = "PlayBar";
      doc.body.appendChild(tenderSticky);

      expect(findSteamStickyPlayBar(doc, inPagePlayBar)).toBeNull();
    });

    it("returns null when no sticky play bar exists", () => {
      const doc = document.implementation.createHTMLDocument("Test");
      const inPagePlayBar = doc.createElement("div");
      inPagePlayBar.className = "InPagePlayBar";
      doc.body.appendChild(inPagePlayBar);

      expect(findSteamStickyPlayBar(doc, inPagePlayBar)).toBeNull();
    });
  });

  describe("findHeroWrapperFallback", () => {
    it("returns matching hero element when it does not contain playBarTop", () => {
      const steamPanel = document.createElement("div");
      const hero = document.createElement("div");
      hero.className = "appDetailsHeroHeader";
      const playBarTop = document.createElement("div");
      steamPanel.appendChild(hero);
      steamPanel.appendChild(playBarTop);

      expect(findHeroWrapperFallback(steamPanel, playBarTop)).toBe(hero);
    });

    it("ignores hero query if it is playBarTop or contains playBarTop", () => {
      const steamPanel = document.createElement("div");
      const hero = document.createElement("div");
      hero.className = "HeroBanner";
      const playBarTop = document.createElement("div");
      hero.appendChild(playBarTop);
      steamPanel.appendChild(hero);

      // Falls through to firstElementChild, which is hero (which also contains playBarTop), so null
      expect(findHeroWrapperFallback(steamPanel, playBarTop)).toBeNull();
    });

    it("falls back to firstElementChild when no hero class matches", () => {
      const steamPanel = document.createElement("div");
      const firstChild = document.createElement("div");
      firstChild.className = "SomeOtherContainer";
      const playBarTop = document.createElement("div");
      steamPanel.appendChild(firstChild);
      steamPanel.appendChild(playBarTop);

      expect(findHeroWrapperFallback(steamPanel, playBarTop)).toBe(firstChild);
    });

    it("returns null when firstElementChild is playBarTop and no hero matches", () => {
      const steamPanel = document.createElement("div");
      const playBarTop = document.createElement("div");
      steamPanel.appendChild(playBarTop);

      expect(findHeroWrapperFallback(steamPanel, playBarTop)).toBeNull();
    });

    it("returns null when steamPanel has no children", () => {
      const steamPanel = document.createElement("div");
      const playBarTop = document.createElement("div");

      expect(findHeroWrapperFallback(steamPanel, playBarTop)).toBeNull();
    });
  });
});
