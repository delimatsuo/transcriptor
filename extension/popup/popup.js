/**
 * popup.js — T.A.R.S. Extension Popup Logic
 *
 * Handles session linking, status display, and session listing.
 */

(() => {
  "use strict";

  const elements = {
    backendUrl: document.getElementById("backend-url"),
    sessionsList: document.getElementById("sessions-list"),
    refreshBtn: document.getElementById("refresh-btn"),
    unlinkBtn: document.getElementById("unlink-btn"),
    statusBadge: document.getElementById("status-badge"),
    syncInfo: document.getElementById("sync-info"),
    syncQuality: document.getElementById("sync-quality"),
    eventsInfo: document.getElementById("events-info"),
    eventCount: document.getElementById("event-count"),
    errorSection: document.getElementById("error-section"),
    errorMessage: document.getElementById("error-message"),
  };

  let currentStatus = null;

  // --- Status polling ---

  function updateStatusDisplay(status) {
    currentStatus = status;

    if (status.linked) {
      elements.statusBadge.textContent = "Linked";
      elements.statusBadge.className = "badge badge-linked";
      elements.unlinkBtn.style.display = "inline-block";

      // Show sync info
      elements.syncInfo.style.display = "flex";
      const uncertainty = parseFloat(status.clockUncertainty);
      elements.syncQuality.textContent = `±${(uncertainty * 1000).toFixed(0)}ms`;
      if (uncertainty > 2.0) {
        elements.syncQuality.style.color = "#ff3b30";
      } else if (uncertainty > 0.5) {
        elements.syncQuality.style.color = "#ff9500";
      } else {
        elements.syncQuality.style.color = "#34c759";
      }

      // Show pending events
      elements.eventsInfo.style.display = "flex";
      elements.eventCount.textContent = status.pendingEvents;

      // Show linked session info
      elements.sessionsList.innerHTML = `
        <div class="linked-session">Linked to session ${status.sessionId.substring(0, 8)}...</div>
      `;
    } else {
      elements.statusBadge.textContent = "Disconnected";
      elements.statusBadge.className = "badge badge-disconnected";
      elements.unlinkBtn.style.display = "none";
      elements.syncInfo.style.display = "none";
      elements.eventsInfo.style.display = "none";
    }
  }

  async function pollStatus() {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({ type: "get_status" }, (response) => {
        if (response) {
          updateStatusDisplay(response);
        }
        resolve(response);
      });
    });
  }

  // --- Session listing ---

  async function fetchSessions() {
    const url = elements.backendUrl.value.replace(/\/$/, "");

    try {
      const response = await fetch(`${url}/api/sessions`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      return data.sessions || [];
    } catch (e) {
      showError(`Cannot reach backend: ${e.message}`);
      return [];
    }
  }

  function renderSessions(sessions) {
    if (currentStatus && currentStatus.linked) return; // Don't overwrite linked display

    if (sessions.length === 0) {
      elements.sessionsList.innerHTML = '<p class="hint">No active sessions found</p>';
      return;
    }

    // Filter to active sessions
    const activeSessions = sessions.filter((s) => s.status === "active");

    if (activeSessions.length === 0) {
      elements.sessionsList.innerHTML = '<p class="hint">No active sessions</p>';
      return;
    }

    elements.sessionsList.innerHTML = activeSessions
      .map((s) => {
        const title = s.title || "Untitled";
        const mode = s.mode || "meeting";
        const id = s.id || "";
        return `
        <div class="session-item">
          <div class="session-info">
            <div class="session-title">${escapeHtml(title)}</div>
            <div class="session-meta">${escapeHtml(mode)} · ${id.substring(0, 8)}...</div>
          </div>
          <button class="btn btn-primary btn-sm link-btn" data-session-id="${escapeHtml(id)}">
            Link
          </button>
        </div>
      `;
      })
      .join("");

    // Attach link handlers
    elements.sessionsList.querySelectorAll(".link-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        linkSession(btn.dataset.sessionId);
      });
    });
  }

  // --- Session linking ---

  async function linkSession(sessionId) {
    const url = elements.backendUrl.value.replace(/\/$/, "");
    clearError();

    // Disable all link buttons during linking
    elements.sessionsList.querySelectorAll(".link-btn").forEach((btn) => {
      btn.disabled = true;
      btn.textContent = "Linking...";
    });

    chrome.runtime.sendMessage(
      { type: "link_session", sessionId, backendUrl: url },
      (response) => {
        if (response && response.ok) {
          pollStatus();
        } else {
          showError(response ? response.error : "Failed to link session");
          refreshSessions();
        }
      }
    );
  }

  function unlinkSession() {
    chrome.runtime.sendMessage({ type: "unlink_session" }, () => {
      pollStatus();
      refreshSessions();
    });
  }

  // --- Helpers ---

  async function refreshSessions() {
    elements.refreshBtn.disabled = true;
    elements.refreshBtn.textContent = "Loading...";

    const sessions = await fetchSessions();
    renderSessions(sessions);

    elements.refreshBtn.disabled = false;
    elements.refreshBtn.textContent = "Refresh";
  }

  function showError(msg) {
    elements.errorSection.style.display = "block";
    elements.errorMessage.textContent = msg;
  }

  function clearError() {
    elements.errorSection.style.display = "none";
    elements.errorMessage.textContent = "";
  }

  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  // --- Event listeners ---

  elements.refreshBtn.addEventListener("click", refreshSessions);
  elements.unlinkBtn.addEventListener("click", unlinkSession);

  // Load saved backend URL
  chrome.storage.session.get("backendUrl", (data) => {
    if (data.backendUrl) {
      elements.backendUrl.value = data.backendUrl;
    }
  });

  // Save backend URL on change
  elements.backendUrl.addEventListener("change", () => {
    chrome.storage.session.set({ backendUrl: elements.backendUrl.value });
  });

  // Listen for status updates from background
  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === "status_update") {
      if (message.status === "clock_error") {
        showError(message.message);
      }
    }
  });

  // Initial load
  pollStatus().then((status) => {
    if (!status || !status.linked) {
      refreshSessions();
    }
  });
})();
