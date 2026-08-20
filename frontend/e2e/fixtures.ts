import type { Page } from "@playwright/test";

export const SESSION_ID = "e2e000000000000000000000000000ff";

/**
 * Mocks every backend call the app makes, so no real session is ever created.
 * Starting a real session opens the machine's physical microphone — tests must
 * never do that.
 *
 * Returns a `send` function that pushes a server frame to the page.
 */
export async function mockSession(page: Page, mode: "meeting" | "interview") {
  await page.route("**/api/sessions**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ session_id: SESSION_ID, mode, status: "active" }),
      });
      return;
    }
    if (url.pathname.endsWith("/recent-interviews")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ interviews: [] }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ sessions: [] }),
    });
  });

  let sendFrame: ((data: string) => void) | null = null;
  let markSocketReady: () => void;
  const socketReady = new Promise<void>((resolve) => {
    markSocketReady = resolve;
  });

  await page.routeWebSocket(/\/ws\//, (ws) => {
    sendFrame = (data: string) => ws.send(data);
    markSocketReady();
    ws.onMessage(() => {
      // client keepalives — ignore
    });
  });

  let seq = 0;

  return {
    async transcript(text: string, speaker: string, isFinal: boolean) {
      await socketReady;
      seq += 1;
      sendFrame?.(
        JSON.stringify({
          type: "transcript_delta",
          session_id: SESSION_ID,
          sequence_number: seq,
          timestamp: new Date(0).toISOString(),
          payload: {
            segment: {
              id: `seg-${seq}`,
              text,
              speaker,
              start_time: seq,
              end_time: seq + 1,
              confidence: 0.9,
              sequence_number: seq,
              is_final: isFinal,
            },
          },
        }),
      );
    },
    async suggestion(questions: string[], markdown = "") {
      await socketReady;
      seq += 1;
      sendFrame?.(
        JSON.stringify({
          type: "suggestion",
          session_id: SESSION_ID,
          sequence_number: seq,
          timestamp: new Date(0).toISOString(),
          payload: { questions, markdown, context: "" },
        }),
      );
    },
  };
}
