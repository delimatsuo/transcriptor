"use client";

import BriefingDisplay from "@/components/BriefingDisplay";

interface Props {
  preInterviewBriefing: string;
}

export default function PreSessionView({ preInterviewBriefing }: Props) {
  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        alignItems: preInterviewBriefing ? "stretch" : "center",
        justifyContent: preInterviewBriefing ? "flex-start" : "center",
        gap: 8,
        padding: preInterviewBriefing ? "24px 28px" : 40,
        overflowY: "auto",
      }}
    >
      {preInterviewBriefing ? (
        <BriefingDisplay markdown={preInterviewBriefing} />
      ) : (
        <>
          <h2
            style={{
              fontSize: 28,
              fontWeight: 600,
              color: "#1d1d1f",
              margin: 0,
              letterSpacing: "-0.5px",
            }}
          >
            Ready to begin
          </h2>
          <p
            style={{
              fontSize: 15,
              color: "#86868b",
              margin: 0,
              textAlign: "center",
              maxWidth: 400,
              lineHeight: 1.5,
            }}
          >
            Configure your session above and start recording. T.A.R.S. will
            transcribe and assist in real time.
          </p>
        </>
      )}
    </div>
  );
}
