/**
 * background.js — MV3 Service Worker for T.A.R.S. Speaker Detection.
 *
 * Handles: NTP-style clock sync, event batching, heartbeat forwarding,
 * session token auth, and disconnect recovery.
 */

(() => {
  "use strict";

  // --- State (survives within SW lifetime, persisted to storage for recovery) ---
  let linkedSessionId = null;
  let backendUrl = "http://localhost:8000";
  let sessionToken = null;
  let clockOffset = 0; // seconds: server_time - client_time
  let clockUncertainty = 0.5; // seconds
  let eventBatch = [];
  let flushTimer = null;

  const FLUSH_INTERVAL_MS = 500;
  const SYNC_INTERVAL_MINUTES = 1;
  const SYNC_SAMPLES = 5;
  const MAX_CLOCK_UNCERTAINTY = 5.0;
  const WARN_CLOCK_UNCERTAINTY = 2.0;

  // --- Persistence helpers ---

  async function saveState() {
    await chrome.storage.session.set({
      linkedSessionId,
      backendUrl,
      sessionToken,
      clockOffset,
      clockUncertainty,
    });
  }

  async function loadState() {
    const data = await chrome.storage.session.get([
      "linkedSessionId",
      "backendUrl",
      "sessionToken",
      "clockOffset",
      "clockUncertainty",
    ]);
    if (data.linkedSessionId) linkedSessionId = data.linkedSessionId;
    if (data.backendUrl) backendUrl = data.backendUrl;
    if (data.sessionToken) sessionToken = data.sessionToken;
    if (data.clockOffset !== undefined) clockOffset = data.clockOffset;
    if (data.clockUncertainty !== undefined) clockUncertainty = data.clockUncertainty;
  }

  async function persistUnsentEvents() {
    if (eventBatch.length > 0) {
      await chrome.storage.local.set({ unsentEvents: [...eventBatch] });
    }
  }

  async function recoverUnsentEvents() {
    const data = await chrome.storage.local.get("unsentEvents");
    if (data.unsentEvents && data.unsentEvents.length > 0) {
      eventBatch.push(...data.unsentEvents);
      await chrome.storage.local.remove("unsentEvents");
    }
  }

  // --- API helpers ---

  function apiHeaders() {
    const headers = { "Content-Type": "application/json" };
    if (sessionToken) {
      headers["Authorization"] = `Bearer ${sessionToken}`;
    }
    return headers;
  }

  async function apiPost(path, body) {
    const url = `${backendUrl}/api/sessions/${linkedSessionId}${path}`;
    const response = await fetch(url, {
      method: "POST",
      headers: apiHeaders(),
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`API error ${response.status}: ${await response.text()}`);
    }
    return response.json();
  }

  // --- Clock Synchronization (NTP-style) ---

  async function performClockSync() {
    if (!linkedSessionId) return;

    let bestOffset = 0;
    let minRtt = Infinity;

    for (let i = 0; i < SYNC_SAMPLES; i++) {
      const clientSendTime = Date.now() / 1000;

      try {
        const response = await apiPost("/clock-sync", {
          client_send_time: clientSendTime,
        });

        const clientReceiveTime = Date.now() / 1000;
        const rtt = clientReceiveTime - clientSendTime;
        const offset = response.server_time - clientSendTime - rtt / 2;

        if (rtt < minRtt) {
          minRtt = rtt;
          bestOffset = offset;
        }
      } catch (e) {
        console.warn("[T.A.R.S.] Clock sync sample failed:", e.message);
      }

      // Small delay between samples
      if (i < SYNC_SAMPLES - 1) {
        await new Promise((r) => setTimeout(r, 200));
      }
    }

    if (minRtt < Infinity) {
      clockOffset = bestOffset;
      clockUncertainty = minRtt / 2;

      if (clockUncertainty > MAX_CLOCK_UNCERTAINTY) {
        console.error("[T.A.R.S.] Clock uncertainty too high:", clockUncertainty);
        // Notify popup about clock issues
        chrome.runtime.sendMessage({
          type: "status_update",
          status: "clock_error",
          message: `Clock sync uncertainty too high: ${clockUncertainty.toFixed(1)}s`,
        }).catch(() => {});
      } else if (clockUncertainty > WARN_CLOCK_UNCERTAINTY) {
        console.warn("[T.A.R.S.] Clock uncertainty high:", clockUncertainty);
      }

      await saveState();
      console.log(
        `[T.A.R.S.] Clock synced: offset=${clockOffset.toFixed(3)}s, uncertainty=${clockUncertainty.toFixed(3)}s, minRTT=${minRtt.toFixed(3)}s`
      );
    }
  }

  // --- Event Batching ---

  function adjustTimestamp(clientTimestamp) {
    // Convert client timestamp to server-relative time
    return clientTimestamp + clockOffset;
  }

  async function flushEvents() {
    if (!linkedSessionId || eventBatch.length === 0) return;

    if (clockUncertainty > MAX_CLOCK_UNCERTAINTY) {
      // Don't send events with unreliable clock
      console.warn("[T.A.R.S.] Skipping flush — clock uncertainty too high");
      return;
    }

    const batch = [...eventBatch];
    eventBatch = [];

    try {
      await apiPost("/active-speaker", { events: batch });
    } catch (e) {
      console.warn("[T.A.R.S.] Failed to flush events:", e.message);
      // Put events back for retry
      eventBatch.unshift(...batch);
      await persistUnsentEvents();
    }
  }

  function startFlushTimer() {
    if (flushTimer) return;
    flushTimer = setInterval(flushEvents, FLUSH_INTERVAL_MS);
  }

  function stopFlushTimer() {
    if (flushTimer) {
      clearInterval(flushTimer);
      flushTimer = null;
    }
  }

  // --- Message handling from content scripts ---

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!linkedSessionId) return;

    switch (message.type) {
      case "active_speaker_change": {
        const adjustedTimestamp = adjustTimestamp(message.timestamp);
        eventBatch.push({
          participant_name: message.participantName,
          timestamp: adjustedTimestamp,
        });
        break;
      }

      case "participants_update": {
        // Forward to backend
        apiPost("/participants", {
          participants: message.participants,
        }).catch((e) => {
          console.warn("[T.A.R.S.] Failed to send participants:", e.message);
        });
        break;
      }

      case "heartbeat": {
        apiPost("/heartbeat", {
          can_detect_speaker: message.canDetectSpeaker,
        }).catch((e) => {
          console.warn("[T.A.R.S.] Failed to send heartbeat:", e.message);
        });
        break;
      }

      // Messages from popup
      case "link_session": {
        handleLinkSession(message.sessionId, message.backendUrl)
          .then((result) => sendResponse(result))
          .catch((e) => sendResponse({ ok: false, error: e.message }));
        return true; // async response
      }

      case "unlink_session": {
        handleUnlinkSession();
        sendResponse({ ok: true });
        break;
      }

      case "get_status": {
        sendResponse({
          linked: !!linkedSessionId,
          sessionId: linkedSessionId,
          backendUrl,
          clockOffset: clockOffset.toFixed(3),
          clockUncertainty: clockUncertainty.toFixed(3),
          pendingEvents: eventBatch.length,
        });
        return false;
      }
    }
  });

  // --- Session linking ---

  async function handleLinkSession(sessionId, url) {
    backendUrl = url || backendUrl;
    linkedSessionId = sessionId;

    // Get session token
    try {
      const response = await fetch(
        `${backendUrl}/api/sessions/${sessionId}/extension-link`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
        }
      );
      if (!response.ok) {
        throw new Error(`Link failed: ${response.status}`);
      }
      const data = await response.json();
      sessionToken = data.token;
    } catch (e) {
      linkedSessionId = null;
      throw e;
    }

    await saveState();

    // Perform initial clock sync
    await performClockSync();

    // Start event flushing
    startFlushTimer();

    // Recover any unsent events from previous session
    await recoverUnsentEvents();

    // Set up periodic clock re-sync
    chrome.alarms.create("clock-sync", { periodInMinutes: SYNC_INTERVAL_MINUTES });

    console.log(`[T.A.R.S.] Linked to session ${sessionId}`);
    return { ok: true, token: sessionToken };
  }

  function handleUnlinkSession() {
    stopFlushTimer();
    persistUnsentEvents();
    linkedSessionId = null;
    sessionToken = null;
    eventBatch = [];
    chrome.alarms.clear("clock-sync");
    saveState();
    console.log("[T.A.R.S.] Unlinked from session");
  }

  // --- Alarms (keep-alive + periodic sync) ---

  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "clock-sync") {
      performClockSync();
    } else if (alarm.name === "keep-alive") {
      // Just waking up the service worker
    }
  });

  // Keep service worker alive during active session
  chrome.alarms.create("keep-alive", { periodInMinutes: 0.4 }); // ~24s

  // --- Startup ---

  loadState().then(() => {
    if (linkedSessionId) {
      console.log(`[T.A.R.S.] Restored session link: ${linkedSessionId}`);
      startFlushTimer();
      recoverUnsentEvents();
    }
  });
})();
