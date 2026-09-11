/**
 * meet-prompt.js — Content script for automated Google Meet interview recognition.
 *
 * Checks current room code against Transcriptor Calendar Monitor via background worker.
 * If matched with a scheduled interview, presents a non-intrusive floating prompt card
 * to launch Transcriptor with preloaded candidate and job details.
 */

(() => {
  "use strict";

  // Check if current path matches Google Meet room pattern: /xxx-yyyy-zzz
  const path = window.location.pathname;
  const match = path.match(/^\/([a-z]{3}-[a-z]{4}-[a-z]{3})/i);
  if (!match) return;

  const meetCode = match[1].toLowerCase();
  const sessionKey = `transcriptor_meet_prompt_dismissed_${meetCode}`;

  if (sessionStorage.getItem(sessionKey)) {
    return;
  }

  function escapeHtml(str) {
    if (!str) return "";
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function renderPrompt(interview) {
    if (document.getElementById("transcriptor-meet-prompt-root")) return;

    const candidateName = interview.candidate_name || "Candidato";
    const jobTitle = interview.job_title || "Entrevista";
    let startTimeStr = "Agora";
    if (interview.starts_at) {
      try {
        const d = new Date(interview.starts_at);
        if (!isNaN(d.getTime())) {
          startTimeStr = d.toLocaleTimeString("pt-BR", {
            hour: "2-digit",
            minute: "2-digit",
          });
        }
      } catch {
        // Fallback to "Agora"
      }
    }

    const candidateIdParam = interview.candidate_id
      ? `&candidate_id=${encodeURIComponent(interview.candidate_id)}`
      : "";
    const transcriptorUrl = `http://localhost:3003/?candidate=${encodeURIComponent(
      candidateName,
    )}&job=${encodeURIComponent(jobTitle)}&meet=${encodeURIComponent(meetCode)}${candidateIdParam}&open=1`;

    const root = document.createElement("div");
    root.id = "transcriptor-meet-prompt-root";
    root.innerHTML = `
      <div class="transcriptor-prompt-header">
        <span class="transcriptor-badge"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: -1px; margin-right: 4px;"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" x2="12" y1="19" y2="22"></line></svg>Transcriptor Copiloto</span>
        <button class="transcriptor-close-btn" id="transcriptor-prompt-close" title="Fechar">&times;</button>
      </div>
      <div class="transcriptor-prompt-body">
        <h3>Entrevista Detectada</h3>
        <p>Esta reunião coincide com uma entrevista agendada em seu calendário.</p>
        <div class="transcriptor-interview-card">
          <div class="transcriptor-card-row">
            <span class="transcriptor-card-label">Candidato:</span>
            <span class="transcriptor-card-value">${escapeHtml(candidateName)}</span>
          </div>
          <div class="transcriptor-card-row">
            <span class="transcriptor-card-label">Vaga:</span>
            <span class="transcriptor-card-value">${escapeHtml(jobTitle)}</span>
          </div>
          <div class="transcriptor-card-row">
            <span class="transcriptor-card-label">Horário:</span>
            <span class="transcriptor-card-value">${escapeHtml(startTimeStr)}</span>
          </div>
        </div>
      </div>
      <div class="transcriptor-prompt-actions">
        <button class="transcriptor-btn-secondary" id="transcriptor-prompt-dismiss">Agora Não</button>
        <a class="transcriptor-btn-primary" id="transcriptor-prompt-launch" href="${transcriptorUrl}" target="_blank" rel="noopener noreferrer">
          Iniciar Transcrição & Copiloto
        </a>
      </div>
    `;

    document.body.appendChild(root);

    function dismissPrompt() {
      sessionStorage.setItem(sessionKey, "1");
      root.style.opacity = "0";
      root.style.transform = "translateY(-12px)";
      setTimeout(() => {
        if (root.parentNode) {
          root.parentNode.removeChild(root);
        }
      }, 250);
    }

    const closeBtn = root.querySelector("#transcriptor-prompt-close");
    const dismissBtn = root.querySelector("#transcriptor-prompt-dismiss");
    const launchBtn = root.querySelector("#transcriptor-prompt-launch");

    if (closeBtn) closeBtn.addEventListener("click", dismissPrompt);
    if (dismissBtn) dismissBtn.addEventListener("click", dismissPrompt);
    if (launchBtn) {
      launchBtn.addEventListener("click", () => {
        setTimeout(dismissPrompt, 400);
      });
    }
  }

  function checkInterview() {
    try {
      chrome.runtime.sendMessage(
        { type: "CHECK_MEET_INTERVIEW", meetCode },
        (response) => {
          if (chrome.runtime.lastError) {
            // Extension context or service worker wake-up delay
            return;
          }
          if (response && response.matched && response.interview) {
            renderPrompt(response.interview);
          }
        },
      );
    } catch {
      // Ignore extension context errors
    }
  }

  // Probe initially after 1.5 seconds, and once more at 5 seconds (handles slow Meet join preview)
  setTimeout(checkInterview, 1500);
  setTimeout(checkInterview, 5000);
})();
