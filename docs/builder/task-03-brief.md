# Task 03 — Cockpit "Conectar companion" button (deep link)

Read `docs/builder/README.md` first (protocol, hard rules). Repo: `"/Volumes/Extreme Pro/MYPROJECTS/Transcriptor"` (quote — space in path). Frontend is Next.js in `frontend/` (tests: `cd frontend && npm test`, runner is **node:test + node:assert/strict** — NOT Jest; build: `npm run build`). The menu bar app (Tasks 01-02, merged) registers the URL scheme `tars-companion://join?session=<id>&key=<key>[&gateway=<ws-url>]` and auto-starts capture when such a link opens.

## Objective

Replace the terminal-command UX in the cockpit with a one-click deep-link button, keeping the copy-able command as a collapsed fallback.

## Existing code facts

- `frontend/src/components/CompanionCommand.tsx` (from an earlier phase): client component receiving `sessionId: string` and `streamKey: string` props; renders label "Canal do Candidato — execute o companion:", a monospace command line `./tars-companion --session-id <id> --stream-key <key> --sources system_audio`, and a "Copiar" button (navigator.clipboard, "Copiado!" state); returns `null` when `streamKey` is empty. Styled with inline `style={{}}` objects (NO Tailwind in this repo); secondary-label color literal `#515154`.
- It is mounted in `frontend/src/components/InterviewLiveView.tsx` (interview mode, active session only).
- `frontend/src/lib/streamUrl.ts` + `streamUrl.test.ts` show the exact helper+test conventions to imitate (pure function; node:test with `.ts` relative import).
- The browser's WS base env expression used elsewhere: `process.env.NEXT_PUBLIC_WS_STREAM_URL || "ws://127.0.0.1:8000/api/stream/native"`.

## File plan

**Create:**
1. `frontend/src/lib/joinLink.ts`:
```typescript
/** Deep link that the TarsCompanion menu bar app registers (see companion/native-macos). */
export function buildJoinLink(
  sessionId: string,
  streamKey: string,
  gatewayBase?: string,
): string {
  const params = [
    `session=${encodeURIComponent(sessionId)}`,
    `key=${encodeURIComponent(streamKey)}`,
  ];
  if (gatewayBase) params.push(`gateway=${encodeURIComponent(gatewayBase)}`);
  return `tars-companion://join?${params.join("&")}`;
}
```
2. `frontend/src/lib/joinLink.test.ts` — node:test style, minimum cases: with gateway (expect fully encoded, e.g. `gateway=ws%3A%2F%2F127.0.0.1%3A8000%2Fapi%2Fstream%2Fnative`); without gateway (no `&gateway=` present); key with `/+=` characters is percent-encoded (`k%2F%2B%3D`).

**Modify:**
3. `frontend/src/components/CompanionCommand.tsx`:
   - Compute `const gatewayBase = process.env.NEXT_PUBLIC_WS_STREAM_URL || "ws://127.0.0.1:8000/api/stream/native";` and `const joinHref = buildJoinLink(sessionId, streamKey, gatewayBase);`
   - PRIMARY UI (replaces the always-visible command): the label text becomes `"Canal do Candidato:"`, followed by an anchor rendered as a button — `<a href={joinHref}>Conectar companion</a>` styled like the existing button but visually primary (background `#0a84ff`, white text, same border-radius/padding scale as the current "Copiar" button), with `title="Abre o app TarsCompanion e inicia a captura"`.
   - Below it, small secondary text (`#515154`, caption size): `"Não tem o app? Veja o guia de onboarding."` (plain text, no link yet).
   - FALLBACK: wrap the existing monospace command + "Copiar" button in a `<details>` element whose `<summary>` reads `"Método alternativo (terminal)"` — collapsed by default; keep the existing copy behavior and styles unchanged inside.
   - Keep the `if (!streamKey) return null;` guard and the component's props unchanged.

## Constraints

- Touch ONLY the three files above. Do not modify InterviewLiveView.tsx, page.tsx, hooks, or anything in backend/ or companion/.
- pt-BR copy exactly as given. Inline styles only (match the file's existing conventions). Keep hooks order legal (any new state/hooks above the early return).

## Verification (paste real output)

```
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm test        # 56 existing + your new tests, 0 failures
cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/frontend" && npm run build   # must succeed
```
(You cannot click the button headlessly — the designer smoke-tests the click in a browser. Your proof is: tests green, build green, and the rendered `href` value shown in your report for a sample session/key.)

## Report

`docs/builder/task-03-report.md`: files changed, test/build output, the exact `joinHref` produced for `sessionId="abc123"`, `streamKey="k/+="` with the default gateway, and anything uncertain.
