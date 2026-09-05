"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/auth";
import { apiUrl } from "@/lib/runtimeConfig";

interface OnboardingBannerProps {
  onOpenModal: () => void;
}

export default function OnboardingBanner({ onOpenModal }: OnboardingBannerProps) {
  const [dismissed, setDismissed] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(false);

  useEffect(() => {
    const isDismissed = sessionStorage.getItem("tars_onboarding_dismissed");
    if (isDismissed === "true") {
      setDismissed(true);
      return;
    }

    void (async () => {
      try {
        const res = await apiFetch(apiUrl("/api/settings/integrations"));
        if (res.ok) {
          const data = await res.json();
          // If Workable or Calendar is unconfigured, prompt the user
          if (!data.workable?.configured || !data.calendar?.configured) {
            setNeedsSetup(true);
          }
        }
      } catch (e) {
        console.warn("Failed to check integration status for banner:", e);
      }
    })();
  }, []);

  if (dismissed || !needsSetup) return null;

  return (
    <div
      style={{
        margin: "0 0 16px 0",
        padding: "12px 18px",
        backgroundColor: "#f0f7ff",
        border: "1px solid #cce5ff",
        borderRadius: 12,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 14,
        boxShadow: "0 1px 3px rgba(0, 122, 255, 0.08)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ fontSize: 22 }}>✨</span>
        <div>
          <strong style={{ fontSize: 13, color: "#004085", display: "block" }}>
            Conecte suas ferramentas de recrutamento
          </strong>
          <span style={{ fontSize: 12, color: "#0056b3" }}>
            Vincule o Workable ATS e o Google Calendar para preenchimento de currículos, vagas e detecção de reuniões em 1 clique.
          </span>
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
        <button
          type="button"
          onClick={onOpenModal}
          style={{
            padding: "6px 14px",
            backgroundColor: "#007aff",
            color: "#ffffff",
            border: "none",
            borderRadius: 8,
            fontSize: 12,
            fontWeight: 500,
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          Configurar Conexões
        </button>
        <button
          type="button"
          onClick={() => {
            setDismissed(true);
            sessionStorage.setItem("tars_onboarding_dismissed", "true");
          }}
          style={{
            background: "none",
            border: "none",
            color: "#6c757d",
            fontSize: 14,
            cursor: "pointer",
            padding: "4px 6px",
          }}
          title="Dispensar aviso"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
