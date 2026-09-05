"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/auth";
import { apiUrl } from "@/lib/runtimeConfig";

interface IntegrationsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

type TabType = "workable" | "calendar" | "extension" | "audio";

interface IntegrationStatusResponse {
  ok: boolean;
  workable: {
    configured: boolean;
    subdomain: string | null;
    api_key_masked: string | null;
  };
  calendar: {
    configured: boolean;
    ical_url_masked: string | null;
  };
}

export default function IntegrationsModal({
  isOpen,
  onClose,
  onSaved,
}: IntegrationsModalProps) {
  const [activeTab, setActiveTab] = useState<TabType>("workable");

  // Workable state
  const [workableSubdomain, setWorkableSubdomain] = useState("");
  const [workableApiKey, setWorkableApiKey] = useState("");
  const [workableMaskedKey, setWorkableMaskedKey] = useState<string | null>(null);
  const [workableConfigured, setWorkableConfigured] = useState(false);
  const [testingWorkable, setTestingWorkable] = useState(false);
  const [workableMsg, setWorkableMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Calendar state
  const [calendarIcalUrl, setCalendarIcalUrl] = useState("");
  const [calendarMaskedUrl, setCalendarMaskedUrl] = useState<string | null>(null);
  const [calendarConfigured, setCalendarConfigured] = useState(false);
  const [testingCalendar, setTestingCalendar] = useState(false);
  const [calendarMsg, setCalendarMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Saving state
  const [saving, setSaving] = useState(false);

  const loadSettings = async () => {
    try {
      const res = await apiFetch(apiUrl("/api/settings/integrations"));
      if (res.ok) {
        const data = (await res.json()) as IntegrationStatusResponse;
        setWorkableConfigured(data.workable.configured);
        setWorkableSubdomain(data.workable.subdomain || "");
        setWorkableMaskedKey(data.workable.api_key_masked);
        setCalendarConfigured(data.calendar.configured);
        setCalendarMaskedUrl(data.calendar.ical_url_masked);
      }
    } catch (err) {
      console.warn("Failed to load integration settings:", err);
    }
  };

  useEffect(() => {
    if (!isOpen) return;
    void loadSettings();
  }, [isOpen]);

  const handleTestWorkable = async () => {
    setTestingWorkable(true);
    setWorkableMsg(null);
    try {
      const res = await apiFetch(apiUrl("/api/settings/integrations"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workable_subdomain: workableSubdomain,
          workable_api_key: workableApiKey || undefined,
          test_only: true,
        }),
      });
      const data = await res.json();
      if (data.ok && data.workable?.ok) {
        setWorkableMsg({
          type: "success",
          text: data.workable.message || "Conexão com Workable validada com sucesso!",
        });
      } else {
        setWorkableMsg({
          type: "error",
          text: data.workable?.error || "Falha ao validar credenciais do Workable.",
        });
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setWorkableMsg({ type: "error", text: msg });
    } finally {
      setTestingWorkable(false);
    }
  };

  const handleSaveWorkable = async () => {
    setSaving(true);
    setWorkableMsg(null);
    try {
      const res = await apiFetch(apiUrl("/api/settings/integrations"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workable_subdomain: workableSubdomain,
          workable_api_key: workableApiKey || undefined,
          test_only: false,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setWorkableConfigured(true);
        setWorkableMsg({ type: "success", text: "Configurações do Workable salvas com sucesso!" });
        setWorkableApiKey("");
        await loadSettings();
        onSaved?.();
      } else {
        setWorkableMsg({ type: "error", text: data.error || "Erro ao salvar Workable." });
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setWorkableMsg({ type: "error", text: msg });
    } finally {
      setSaving(false);
    }
  };

  const handleTestCalendar = async () => {
    setTestingCalendar(true);
    setCalendarMsg(null);
    try {
      const res = await apiFetch(apiUrl("/api/settings/integrations"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          calendar_ical_url: calendarIcalUrl,
          test_only: true,
        }),
      });
      const data = await res.json();
      if (data.ok && data.calendar?.ok) {
        setCalendarMsg({
          type: "success",
          text: data.calendar.message || "Calendário validado com sucesso!",
        });
      } else {
        setCalendarMsg({
          type: "error",
          text: data.calendar?.error || "Falha ao acessar o link do calendário.",
        });
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setCalendarMsg({ type: "error", text: msg });
    } finally {
      setTestingCalendar(false);
    }
  };

  const handleSaveCalendar = async () => {
    setSaving(true);
    setCalendarMsg(null);
    try {
      const res = await apiFetch(apiUrl("/api/settings/integrations"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          calendar_ical_url: calendarIcalUrl,
          test_only: false,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setCalendarConfigured(true);
        setCalendarMsg({ type: "success", text: "Link do calendário salvo com sucesso!" });
        setCalendarIcalUrl("");
        await loadSettings();
        onSaved?.();
      } else {
        setCalendarMsg({ type: "error", text: data.error || "Erro ao salvar calendário." });
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setCalendarMsg({ type: "error", text: msg });
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(0, 0, 0, 0.4)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
        padding: 20,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 620,
          backgroundColor: "#ffffff",
          borderRadius: 16,
          boxShadow: "0 20px 40px rgba(0, 0, 0, 0.15)",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          maxHeight: "90vh",
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "18px 24px",
            borderBottom: "1px solid #f0f0f2",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 600, color: "#1d1d1f", margin: 0 }}>
              ⚙️ Conexões & Integrações
            </h2>
            <p style={{ fontSize: 12, color: "#86868b", margin: "4px 0 0 0" }}>
              Configure o Workable, Google Calendar e a extensão do Google Meet
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              fontSize: 18,
              cursor: "pointer",
              color: "#86868b",
              padding: 4,
            }}
          >
            ✕
          </button>
        </div>

        {/* Tab Navigation */}
        <div
          style={{
            display: "flex",
            borderBottom: "1px solid #f0f0f2",
            backgroundColor: "#f9f9fb",
            padding: "0 12px",
          }}
        >
          <button
            type="button"
            onClick={() => setActiveTab("workable")}
            style={{
              padding: "12px 16px",
              border: "none",
              background: "none",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
              color: activeTab === "workable" ? "#007aff" : "#6e6e73",
              borderBottom: activeTab === "workable" ? "2px solid #007aff" : "2px solid transparent",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span>🏢 Workable</span>
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                backgroundColor: workableConfigured ? "#34c759" : "#ff9500",
              }}
            />
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("calendar")}
            style={{
              padding: "12px 16px",
              border: "none",
              background: "none",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
              color: activeTab === "calendar" ? "#007aff" : "#6e6e73",
              borderBottom: activeTab === "calendar" ? "2px solid #007aff" : "2px solid transparent",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span>📅 Calendário</span>
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                backgroundColor: calendarConfigured ? "#34c759" : "#ff9500",
              }}
            />
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("extension")}
            style={{
              padding: "12px 16px",
              border: "none",
              background: "none",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
              color: activeTab === "extension" ? "#007aff" : "#6e6e73",
              borderBottom: activeTab === "extension" ? "2px solid #007aff" : "2px solid transparent",
            }}
          >
            🧩 Extensão Meet
          </button>

          <button
            type="button"
            onClick={() => setActiveTab("audio")}
            style={{
              padding: "12px 16px",
              border: "none",
              background: "none",
              fontSize: 13,
              fontWeight: 500,
              cursor: "pointer",
              color: activeTab === "audio" ? "#007aff" : "#6e6e73",
              borderBottom: activeTab === "audio" ? "2px solid #007aff" : "2px solid transparent",
            }}
          >
            🎙️ Áudio & Companion
          </button>
        </div>

        {/* Tab Content */}
        <div style={{ padding: 24, overflowY: "auto", flex: 1 }}>
          {/* TAB 1: WORKABLE */}
          {activeTab === "workable" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div>
                <h3 style={{ fontSize: 14, fontWeight: 600, color: "#1d1d1f", margin: "0 0 6px 0" }}>
                  Integração Workable ATS
                </h3>
                <p style={{ fontSize: 13, color: "#6e6e73", margin: 0, lineHeight: 1.4 }}>
                  Permite carregar vagas, candidatos, currículos e notas anteriores automaticamente, além de exportar o relatório final estruturado para a timeline do candidato.
                </p>
              </div>

              {workableConfigured && (
                <div
                  style={{
                    padding: "10px 14px",
                    backgroundColor: "#f0fbf4",
                    border: "1px solid #b7eb8f",
                    borderRadius: 8,
                    fontSize: 12,
                    color: "#27ae60",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <span>✓</span>
                  <span>
                    Conexão configurada para <strong>{workableSubdomain}.workable.com</strong> (Token: {workableMaskedKey})
                  </span>
                </div>
              )}

              <div>
                <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#495057", marginBottom: 6 }}>
                  Subdomínio da Empresa
                </label>
                <div style={{ display: "flex", alignItems: "center" }}>
                  <input
                    type="text"
                    value={workableSubdomain}
                    onChange={(e) => setWorkableSubdomain(e.target.value)}
                    placeholder="ex: ellaexecutivesearch"
                    style={{
                      flex: 1,
                      padding: "8px 12px",
                      borderRadius: "8px 0 0 8px",
                      border: "1px solid #d2d2d7",
                      fontSize: 13,
                      outline: "none",
                    }}
                  />
                  <span
                    style={{
                      padding: "8px 12px",
                      backgroundColor: "#f2f2f7",
                      border: "1px solid #d2d2d7",
                      borderLeft: "none",
                      borderRadius: "0 8px 8px 0",
                      fontSize: 13,
                      color: "#6e6e73",
                    }}
                  >
                    .workable.com
                  </span>
                </div>
              </div>

              <div>
                <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#495057", marginBottom: 6 }}>
                  Chave de Acesso da API (Partner Token)
                </label>
                <input
                  type="password"
                  value={workableApiKey}
                  onChange={(e) => setWorkableApiKey(e.target.value)}
                  placeholder={workableMaskedKey ? `Manter atual (${workableMaskedKey})` : "Cole o token de API do Workable"}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    borderRadius: 8,
                    border: "1px solid #d2d2d7",
                    fontSize: 13,
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                />
                <span style={{ display: "block", fontSize: 11, color: "#86868b", marginTop: 4 }}>
                  Obtenha em: Workable &gt; Configurações da Empresa &gt; Integrações &gt; Acesso da API.
                </span>
              </div>

              {workableMsg && (
                <div
                  style={{
                    padding: "10px 14px",
                    borderRadius: 8,
                    fontSize: 12,
                    backgroundColor: workableMsg.type === "success" ? "#f0fbf4" : "#fdf2f2",
                    color: workableMsg.type === "success" ? "#27ae60" : "#d93025",
                    border: `1px solid ${workableMsg.type === "success" ? "#b7eb8f" : "#fcc2c3"}`,
                  }}
                >
                  {workableMsg.text}
                </div>
              )}

              <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
                <button
                  type="button"
                  onClick={handleTestWorkable}
                  disabled={testingWorkable || (!workableApiKey && !workableMaskedKey)}
                  style={{
                    padding: "8px 16px",
                    backgroundColor: "#f2f2f7",
                    color: "#1d1d1f",
                    border: "1px solid #d2d2d7",
                    borderRadius: 8,
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: "pointer",
                  }}
                >
                  {testingWorkable ? "Testando..." : "Testar Conexão"}
                </button>
                <button
                  type="button"
                  onClick={handleSaveWorkable}
                  disabled={saving || !workableSubdomain}
                  style={{
                    padding: "8px 20px",
                    backgroundColor: "#007aff",
                    color: "#ffffff",
                    border: "none",
                    borderRadius: 8,
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: "pointer",
                  }}
                >
                  {saving ? "Salvando..." : "Salvar Configuração"}
                </button>
              </div>
            </div>
          )}

          {/* TAB 2: CALENDAR */}
          {activeTab === "calendar" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div>
                <h3 style={{ fontSize: 14, fontWeight: 600, color: "#1d1d1f", margin: "0 0 6px 0" }}>
                  Google Calendar / Outlook (iCal)
                </h3>
                <p style={{ fontSize: 13, color: "#6e6e73", margin: 0, lineHeight: 1.4 }}>
                  Monitore suas entrevistas agendadas na sua agenda privada para detecção automática de chamadas e preenchimento de sessão.
                </p>
              </div>

              {/* Step-by-Step Guide */}
              <div
                style={{
                  padding: 14,
                  backgroundColor: "#f5f5f7",
                  borderRadius: 10,
                  fontSize: 12,
                  color: "#495057",
                  lineHeight: 1.5,
                }}
              >
                <strong style={{ display: "block", marginBottom: 6, color: "#1d1d1f" }}>
                  Como obter seu link privado no Google Calendar:
                </strong>
                <ol style={{ margin: 0, paddingLeft: 18 }}>
                  <li>Acesse o Google Calendar no navegador.</li>
                  <li>No menu esquerdo, clique nos 3 pontos <strong>⋮</strong> ao lado da sua agenda &gt; <em>Configurações e compart.</em></li>
                  <li>Role até a seção <strong>Integrar agenda</strong>.</li>
                  <li>Copie o link em <strong>&quot;Endereço secreto no formato iCal&quot;</strong> (começa com https://calendar.google.com/...).</li>
                </ol>
              </div>

              {calendarConfigured && (
                <div
                  style={{
                    padding: "10px 14px",
                    backgroundColor: "#f0fbf4",
                    border: "1px solid #b7eb8f",
                    borderRadius: 8,
                    fontSize: 12,
                    color: "#27ae60",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <span>✓</span>
                  <span>Calendário ativo: {calendarMaskedUrl}</span>
                </div>
              )}

              <div>
                <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: "#495057", marginBottom: 6 }}>
                  Endereço secreto no formato iCal (.ics)
                </label>
                <input
                  type="text"
                  value={calendarIcalUrl}
                  onChange={(e) => setCalendarIcalUrl(e.target.value)}
                  placeholder={calendarMaskedUrl ? `Manter atual (${calendarMaskedUrl})` : "https://calendar.google.com/calendar/ical/.../basic.ics"}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    borderRadius: 8,
                    border: "1px solid #d2d2d7",
                    fontSize: 13,
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                />
              </div>

              {calendarMsg && (
                <div
                  style={{
                    padding: "10px 14px",
                    borderRadius: 8,
                    fontSize: 12,
                    backgroundColor: calendarMsg.type === "success" ? "#f0fbf4" : "#fdf2f2",
                    color: calendarMsg.type === "success" ? "#27ae60" : "#d93025",
                    border: `1px solid ${calendarMsg.type === "success" ? "#b7eb8f" : "#fcc2c3"}`,
                  }}
                >
                  {calendarMsg.text}
                </div>
              )}

              <div style={{ display: "flex", gap: 10, marginTop: 8 }}>
                <button
                  type="button"
                  onClick={handleTestCalendar}
                  disabled={testingCalendar || (!calendarIcalUrl && !calendarMaskedUrl)}
                  style={{
                    padding: "8px 16px",
                    backgroundColor: "#f2f2f7",
                    color: "#1d1d1f",
                    border: "1px solid #d2d2d7",
                    borderRadius: 8,
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: "pointer",
                  }}
                >
                  {testingCalendar ? "Testando..." : "Testar Calendário"}
                </button>
                <button
                  type="button"
                  onClick={handleSaveCalendar}
                  disabled={saving || (!calendarIcalUrl && !calendarConfigured)}
                  style={{
                    padding: "8px 20px",
                    backgroundColor: "#007aff",
                    color: "#ffffff",
                    border: "none",
                    borderRadius: 8,
                    fontSize: 12,
                    fontWeight: 500,
                    cursor: "pointer",
                  }}
                >
                  {saving ? "Salvando..." : "Salvar Calendário"}
                </button>
              </div>
            </div>
          )}

          {/* TAB 3: EXTENSION */}
          {activeTab === "extension" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div>
                <h3 style={{ fontSize: 14, fontWeight: 600, color: "#1d1d1f", margin: "0 0 6px 0" }}>
                  Extensão Google Meet (Detector Automático)
                </h3>
                <p style={{ fontSize: 13, color: "#6e6e73", margin: 0, lineHeight: 1.4 }}>
                  A extensão reconhece quando você entra em uma reunião do Google Meet correspondente a uma entrevista na sua agenda e pergunta automaticamente se deseja iniciar a transcrição.
                </p>
              </div>

              <div
                style={{
                  padding: 14,
                  backgroundColor: "#eaf4ff",
                  border: "1px solid #b8daff",
                  borderRadius: 10,
                  fontSize: 12,
                  color: "#004085",
                  lineHeight: 1.5,
                }}
              >
                <strong style={{ display: "block", marginBottom: 6 }}>
                  Como carregar a extensão no Chrome:
                </strong>
                <ol style={{ margin: 0, paddingLeft: 18 }}>
                  <li>Abra <strong>chrome://extensions</strong> no Google Chrome.</li>
                  <li>Ative o <strong>Modo do desenvolvedor</strong> no canto superior direito.</li>
                  <li>Clique em <strong>Carregar sem compactação (Load unpacked)</strong>.</li>
                  <li>
                    Selecione a pasta:
                    <code
                      style={{
                        display: "block",
                        marginTop: 4,
                        padding: "4px 8px",
                        backgroundColor: "#ffffff",
                        borderRadius: 4,
                        border: "1px solid #cce5ff",
                        fontFamily: "monospace",
                        fontSize: 11,
                      }}
                    >
                      /Volumes/Extreme Pro/MYPROJECTS/Transcriptor/extension
                    </code>
                  </li>
                </ol>
              </div>

              <div
                style={{
                  padding: "12px 16px",
                  backgroundColor: "#f9f9fb",
                  borderRadius: 10,
                  border: "1px solid #e5e5ea",
                  fontSize: 12,
                }}
              >
                <span style={{ fontWeight: 600, color: "#1d1d1f" }}>Status de Comunicação:</span>
                <p style={{ margin: "4px 0 0 0", color: "#6e6e73" }}>
                  A extensão se conecta automaticamente ao backend local do Transcriptor na porta <strong>8008</strong> e ao Google Meet.
                </p>
              </div>
            </div>
          )}

          {/* TAB 4: AUDIO */}
          {activeTab === "audio" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div>
                <h3 style={{ fontSize: 14, fontWeight: 600, color: "#1d1d1f", margin: "0 0 6px 0" }}>
                  Captura de Áudio & Companion Nativo
                </h3>
                <p style={{ fontSize: 13, color: "#6e6e73", margin: 0, lineHeight: 1.4 }}>
                  Transcriptor utiliza captura de microfone no navegador e suporte ao aplicativo nativo de barra de menu macOS para gravação sem eco (Process Tap).
                </p>
              </div>

              <div
                style={{
                  padding: 14,
                  backgroundColor: "#f5f5f7",
                  borderRadius: 10,
                  fontSize: 12,
                  color: "#1d1d1f",
                }}
              >
                <span style={{ fontWeight: 600 }}>T.A.R.S. Companion App (macOS):</span>
                <p style={{ margin: "4px 0 0 0", color: "#6e6e73" }}>
                  O aplicativo nativo é executado na barra de menus do macOS e se conecta via gateway na porta <strong>8008</strong> (`ws://127.0.0.1:8008/api/stream/native`).
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "14px 24px",
            borderTop: "1px solid #f0f0f2",
            display: "flex",
            justifyContent: "flex-end",
            backgroundColor: "#f9f9fb",
          }}
        >
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: "8px 18px",
              backgroundColor: "#e5e5ea",
              color: "#1d1d1f",
              border: "none",
              borderRadius: 8,
              fontSize: 12,
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            Fechar
          </button>
        </div>
      </div>
    </div>
  );
}
