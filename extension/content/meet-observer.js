/**
 * meet-observer.js — Content script for Google Meet active speaker detection.
 *
 * Observes participant tiles via MutationObserver, detects active speaker
 * changes, and sends events to the background service worker.
 */

(() => {
  "use strict";

  // --- State ---
  const participants = new Map(); // participantId -> { name, isSelf }
  let selfName = "";
  let lastActiveSpeaker = null;
  let debounceTimer = null;
  let observer = null;
  let healthInterval = null;
  let canDetectSpeaker = false;
  let lastObserverFire = 0;

  const DEBOUNCE_MS = 300;
  const HEALTH_INTERVAL_MS = 10000;
  const SCAN_INTERVAL_MS = 3000;

  // --- Helpers ---

  function sendToBackground(message) {
    try {
      chrome.runtime.sendMessage(message);
    } catch (e) {
      // Extension context invalidated — stop observing
      cleanup();
    }
  }

  function scanParticipants() {
    const tiles = document.querySelectorAll(MeetSelectors.PARTICIPANT_TILE);
    if (tiles.length === 0) return;

    let changed = false;
    const currentIds = new Set();

    tiles.forEach((tile) => {
      const participantId = tile.getAttribute("data-participant-id");
      if (!participantId) return;
      currentIds.add(participantId);

      if (!participants.has(participantId)) {
        const info = MeetSelectors.getNameFromTile(tile);
        if (info) {
          participants.set(participantId, info);
          if (info.isSelf) {
            selfName = info.name;
          }
          changed = true;
        }
      }
    });

    // Also try the participant list sidebar
    const listItems = document.querySelectorAll(MeetSelectors.PARTICIPANT_LIST_ITEM);
    listItems.forEach((item) => {
      const nameEl = item.querySelector(MeetSelectors.PARTICIPANT_LIST_NAME);
      if (nameEl && nameEl.textContent) {
        const name = nameEl.textContent.trim();
        // Check if this name is already tracked
        let found = false;
        for (const [, p] of participants) {
          if (p.name === name) {
            found = true;
            break;
          }
        }
        if (!found && name) {
          participants.set(`list-${name}`, { name, isSelf: false });
          changed = true;
        }
      }
    });

    if (changed) {
      const participantList = [];
      for (const [, p] of participants) {
        participantList.push({ name: p.name, isSelf: p.isSelf });
      }
      sendToBackground({
        type: "participants_update",
        participants: participantList,
      });
    }

    canDetectSpeaker = tiles.length > 0;
  }

  function checkActiveSpeaker() {
    const tiles = document.querySelectorAll(MeetSelectors.PARTICIPANT_TILE);
    let activeSpeakerName = null;

    tiles.forEach((tile) => {
      if (MeetSelectors.isActiveSpeaker(tile)) {
        const participantId = tile.getAttribute("data-participant-id");
        const info = participants.get(participantId);
        if (info && !info.isSelf) {
          activeSpeakerName = info.name;
        } else if (!info) {
          // Try to get name directly from tile
          const tileInfo = MeetSelectors.getNameFromTile(tile);
          if (tileInfo && !tileInfo.isSelf) {
            activeSpeakerName = tileInfo.name;
          }
        }
      }
    });

    return activeSpeakerName;
  }

  function onMutation() {
    lastObserverFire = Date.now();

    // Debounce active speaker changes
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }

    debounceTimer = setTimeout(() => {
      const activeSpeaker = checkActiveSpeaker();

      if (activeSpeaker !== lastActiveSpeaker) {
        lastActiveSpeaker = activeSpeaker;

        if (activeSpeaker) {
          console.log("[T.A.R.S.] active speaker detected:", activeSpeaker);
          sendToBackground({
            type: "active_speaker_change",
            participantName: activeSpeaker,
            timestamp: Date.now() / 1000,
          });
        }
      }
    }, DEBOUNCE_MS);
  }

  function startObserver() {
    // Find the container for video tiles
    const container =
      document.querySelector(MeetSelectors.MEETING_CONTAINER) ||
      document.querySelector(MeetSelectors.TILE_CONTAINER) ||
      document.body;

    observer = new MutationObserver((mutations) => {
      // Filter for relevant changes (attribute changes on tiles, child additions)
      let relevant = false;
      for (const mutation of mutations) {
        if (
          mutation.type === "attributes" &&
          (mutation.attributeName === "class" ||
            mutation.attributeName === "style")
        ) {
          relevant = true;
          break;
        }
        if (mutation.type === "childList") {
          relevant = true;
          break;
        }
      }
      if (relevant) {
        onMutation();
      }
    });

    observer.observe(container, {
      attributes: true,
      attributeFilter: ["class", "style"],
      childList: true,
      subtree: true,
    });

    canDetectSpeaker = true;
    console.log("[T.A.R.S.] MutationObserver started on", container.tagName);
  }

  function startHealthHeartbeat() {
    healthInterval = setInterval(() => {
      // If observer hasn't fired recently and no tiles exist, mark unhealthy
      const tiles = document.querySelectorAll(MeetSelectors.PARTICIPANT_TILE);
      const recentlyFired = Date.now() - lastObserverFire < 30000;
      const currentCanDetect = tiles.length > 0 && (recentlyFired || lastObserverFire === 0);

      canDetectSpeaker = currentCanDetect;

      sendToBackground({
        type: "heartbeat",
        canDetectSpeaker: canDetectSpeaker,
      });
    }, HEALTH_INTERVAL_MS);
  }

  function cleanup() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    if (healthInterval) {
      clearInterval(healthInterval);
      healthInterval = null;
    }
    if (debounceTimer) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
  }

  // --- Initialization ---

  function init() {
    console.log(`[T.A.R.S.] Meet Observer v${MeetSelectors.VERSION} initializing...`);

    // Initial DOM validation
    const validation = MeetSelectors.validate();
    if (!validation.ok) {
      console.log("[T.A.R.S.] Meet elements not found yet, will retry...");
    }

    // Initial participant scan
    scanParticipants();

    // Start observing DOM for active speaker changes
    startObserver();

    // Periodic participant rescan (handles late joiners)
    setInterval(scanParticipants, SCAN_INTERVAL_MS);

    // Health heartbeat
    startHealthHeartbeat();

    // Send initial heartbeat
    sendToBackground({
      type: "heartbeat",
      canDetectSpeaker: canDetectSpeaker,
    });

    console.log("[T.A.R.S.] Meet Observer initialized.");
  }

  // Wait for Meet to fully load before initializing
  if (document.readyState === "complete") {
    setTimeout(init, 2000); // Meet needs time to render tiles
  } else {
    window.addEventListener("load", () => setTimeout(init, 2000));
  }
})();
