"use client";

import { useRef, useState } from "react";

const API_BASE = "http://localhost:8000";

interface Props {
  sessionId: string;
}

export default function DocumentUpload({ sessionId }: Props) {
  const [resumeUploaded, setResumeUploaded] = useState(false);
  const [jdUploaded, setJdUploaded] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resumeRef = useRef<HTMLInputElement>(null);
  const jdRef = useRef<HTMLInputElement>(null);

  const uploadFile = async (file: File, docType: "resume" | "jd") => {
    setUploading(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      formData.append("doc_type", docType);

      const res = await fetch(
        `${API_BASE}/api/sessions/${sessionId}/documents`,
        { method: "POST", body: formData },
      );

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || "Upload failed");
      }

      if (docType === "resume") setResumeUploaded(true);
      else setJdUploaded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleFileChange =
    (docType: "resume" | "jd") =>
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) uploadFile(file, docType);
    };

  return (
    <div
      style={{
        padding: 16,
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        backgroundColor: "#f9fafb",
      }}
    >
      <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
        Interview Documents
      </h3>

      <div style={{ display: "flex", gap: 12 }}>
        <div>
          <input
            ref={resumeRef}
            type="file"
            accept=".pdf,.docx"
            onChange={handleFileChange("resume")}
            style={{ display: "none" }}
          />
          <button
            onClick={() => resumeRef.current?.click()}
            disabled={uploading || resumeUploaded}
            style={{
              padding: "6px 14px",
              border: "1px solid #d1d5db",
              borderRadius: 6,
              backgroundColor: resumeUploaded ? "#d1fae5" : "white",
              cursor: resumeUploaded ? "default" : "pointer",
              fontSize: 13,
            }}
          >
            {resumeUploaded ? "Resume uploaded" : "Upload Resume"}
          </button>
        </div>

        <div>
          <input
            ref={jdRef}
            type="file"
            accept=".pdf,.docx"
            onChange={handleFileChange("jd")}
            style={{ display: "none" }}
          />
          <button
            onClick={() => jdRef.current?.click()}
            disabled={uploading || jdUploaded}
            style={{
              padding: "6px 14px",
              border: "1px solid #d1d5db",
              borderRadius: 6,
              backgroundColor: jdUploaded ? "#d1fae5" : "white",
              cursor: jdUploaded ? "default" : "pointer",
              fontSize: 13,
            }}
          >
            {jdUploaded ? "JD uploaded" : "Upload Job Description"}
          </button>
        </div>
      </div>

      {uploading && (
        <p style={{ fontSize: 12, color: "#6b7280", marginTop: 8 }}>
          Uploading...
        </p>
      )}
      {error && (
        <p style={{ fontSize: 12, color: "#dc2626", marginTop: 8 }}>{error}</p>
      )}
    </div>
  );
}
