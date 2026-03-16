import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "T.A.R.S. — Meeting Assistant",
  description: "Real-time meeting transcription and interview co-pilot",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="pt-BR">
      <body
        style={{
          margin: 0,
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
          backgroundColor: "#ffffff",
          color: "#111827",
        }}
      >
        {children}
        <style>{`
          @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
          }
        `}</style>
      </body>
    </html>
  );
}
