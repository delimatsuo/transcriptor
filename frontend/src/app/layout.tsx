import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "T.A.R.S. — Interview Assistant",
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
            '-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", system-ui, "Helvetica Neue", Arial, sans-serif',
          backgroundColor: "#ffffff",
          color: "#1d1d1f",
          WebkitFontSmoothing: "antialiased",
          MozOsxFontSmoothing: "grayscale",
        }}
      >
        {children}
        <style>{`
          @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.4; }
          }
          @keyframes subtlePulse {
            0%, 100% { opacity: 0.6; }
            50% { opacity: 0.3; }
          }

          /* macOS-like thin scrollbar */
          ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
          }
          ::-webkit-scrollbar-track {
            background: transparent;
          }
          ::-webkit-scrollbar-thumb {
            background: rgba(0, 0, 0, 0.15);
            border-radius: 3px;
          }
          ::-webkit-scrollbar-thumb:hover {
            background: rgba(0, 0, 0, 0.25);
          }

          /* Selection color */
          ::selection {
            background: rgba(0, 122, 255, 0.2);
          }

          * {
            box-sizing: border-box;
          }

          input, textarea, select, button {
            font-family: inherit;
          }

          .client-report-print {
            display: none;
          }

          @media print {
            @page {
              size: A4;
              margin: 22mm 20mm;
            }

            body * {
              visibility: hidden !important;
            }

            .client-report-print,
            .client-report-print * {
              visibility: visible !important;
            }

            .client-report-print {
              display: block !important;
              position: absolute;
              inset: 0;
              color: #000;
              font-family: Georgia, "Times New Roman", serif;
              font-size: 11pt;
              line-height: 1.55;
            }

            .client-report-print p {
              margin: 0 0 14pt;
              text-align: justify;
              orphans: 3;
              widows: 3;
            }
          }
        `}</style>
      </body>
    </html>
  );
}
