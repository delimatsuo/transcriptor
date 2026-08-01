/**
 * meet-selectors.js — Isolated CSS selectors for Google Meet DOM.
 *
 * All Meet DOM queries are centralized here so that when Google
 * changes their markup, only this file needs updating.
 */

// eslint-disable-next-line no-unused-vars
const MeetSelectors = (() => {
  "use strict";

  const VERSION = "1.0.0";

  // --- Selectors ---

  // Participant video tiles (each has a data-participant-id)
  const PARTICIPANT_TILE = "div[data-participant-id]";

  // The container that holds all video tiles
  const TILE_CONTAINER = "[data-allocation-index]";

  // Active speaker indicator: Meet adds a colored border to the speaking tile.
  // The border is applied on a child div inside the tile.
  const ACTIVE_SPEAKER_BORDER_CLASS = "OssEjf";

  // Self participant name attribute
  const SELF_NAME_ATTR = "data-self-name";

  // Participant name within a tile — the tooltip or aria-label on the name element
  const PARTICIPANT_NAME_IN_TILE = "[data-self-name]";

  // Name overlay inside tiles (fallback)
  const PARTICIPANT_NAME_OVERLAY = "div[data-participant-id] [class*='ZjFb7c']";

  // Participant list sidebar items
  const PARTICIPANT_LIST_ITEM = "div[role='listitem']";

  // Participant name within list item
  const PARTICIPANT_LIST_NAME = "span.zWGUib";

  // The main meeting area that contains tiles
  const MEETING_CONTAINER = "[data-meeting-title]";

  /**
   * Validate that key elements exist in the DOM.
   * Returns { ok: boolean, missing: string[] }
   */
  function validate() {
    const missing = [];

    if (!document.querySelector(PARTICIPANT_TILE) && !document.querySelector(MEETING_CONTAINER)) {
      missing.push("PARTICIPANT_TILE or MEETING_CONTAINER");
    }

    if (missing.length > 0) {
      console.warn(
        `[T.A.R.S. Meet Selectors v${VERSION}] Expected elements missing:`,
        missing
      );
    }

    return { ok: missing.length === 0, missing };
  }

  /**
   * Extract participant name from a tile element.
   */
  function getNameFromTile(tileEl) {
    // Try data-self-name first
    const selfName = tileEl.querySelector(`[${SELF_NAME_ATTR}]`);
    if (selfName) {
      return {
        name: selfName.getAttribute(SELF_NAME_ATTR),
        isSelf: true,
      };
    }

    // Try aria-label on the tile itself
    const ariaLabel = tileEl.getAttribute("aria-label");
    if (ariaLabel) {
      return { name: ariaLabel, isSelf: false };
    }

    // Try name overlay
    const overlay = tileEl.querySelector(PARTICIPANT_NAME_OVERLAY.split(" ").pop());
    if (overlay && overlay.textContent) {
      return { name: overlay.textContent.trim(), isSelf: false };
    }

    return null;
  }

  /**
   * Check if a tile element has the active speaker indicator.
   */
  function isActiveSpeaker(tileEl) {
    // Method 1: Check for the active speaker border class
    if (tileEl.querySelector(`.${ACTIVE_SPEAKER_BORDER_CLASS}`)) {
      return true;
    }

    // Method 2: Check for a blue/colored border on the tile's container
    const style = window.getComputedStyle(tileEl);
    const borderColor = style.borderColor || style.outlineColor;
    if (borderColor && borderColor !== "rgb(0, 0, 0)" && borderColor !== "transparent" && borderColor !== "") {
      // Meet uses a blue-ish border for active speaker
      return true;
    }

    // Method 3: Check child elements for border changes
    for (const child of tileEl.children) {
      const childStyle = window.getComputedStyle(child);
      if (childStyle.borderColor && childStyle.borderColor !== "transparent" &&
          childStyle.borderColor !== "rgb(0, 0, 0)" && childStyle.borderWidth !== "0px") {
        const rgb = childStyle.borderColor.match(/\d+/g);
        if (rgb && parseInt(rgb[0]) < 100 && parseInt(rgb[1]) > 100 && parseInt(rgb[2]) > 200) {
          return true;
        }
      }
    }

    return false;
  }

  return {
    VERSION,
    PARTICIPANT_TILE,
    TILE_CONTAINER,
    ACTIVE_SPEAKER_BORDER_CLASS,
    SELF_NAME_ATTR,
    PARTICIPANT_NAME_IN_TILE,
    PARTICIPANT_NAME_OVERLAY,
    PARTICIPANT_LIST_ITEM,
    PARTICIPANT_LIST_NAME,
    MEETING_CONTAINER,
    validate,
    getNameFromTile,
    isActiveSpeaker,
  };
})();
