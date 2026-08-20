# Product Strategist Report — T.A.R.S. Launch Scoping

## 1. Competitive scan (verified August 2026)

**Metaview** has pivoted hard from note-taking toward full AI-agent recruiting: its public pricing is now sourcing-based (Free / Pro $100/user/mo for 200 sourced profiles / Max $300 / Enterprise), with Notes and Reports sold inside a custom-priced "Platform Enterprise" suite ([metaview.ai/pricing](https://www.metaview.ai/pricing)). Notes are post-hoc structured summaries delivered minutes after the call via VC/phone integrations plus Mac/Windows desktop apps ([WebCatalog listing](https://webcatalog.io/en/apps/metaview), [what-is-Metaview](https://support.metaview.ai/get-started/what-is-metaview)); it supports 50+ languages including Portuguese ([Metaview blog](https://www.metaview.ai/resources/blog/metaview-now-supports-50-languages)) and is actively marketing to executive search ([exec-search strategy post](https://www.metaview.ai/resources/blog/executive-search-strategy)).

**BrightHire** was **acquired by Zoom (closed Dec 2025)** ([Zoom blog](https://www.zoom.com/en/blog/zoom-acquires-brighthire/)). It is the strongest live-mode competitor: in-meeting interview guides, real-time prompts and in-call scoring via a Zoom Meetings app ([BrightHire blog](https://brighthire.com/blog/introducing-the-new-brighthire-app-for-zoom/)), plus AI notes, evidence-based debriefs, and an async AI screener; pricing is unpublished, seat + interview-volume based ([Vendr](https://www.vendr.com/marketplace/brighthire), [SelectHub](https://www.selecthub.com/p/video-interview-software/brighthire/)). Expect it to become Zoom-native — a structural weakness for exec search, where interviews happen on the candidate's platform or by phone.

**Generic encroachers.** Granola captures system audio with **no bot**, now on Mac + Windows + iOS, at $14–35/user/mo ([alfred pricing analysis](https://get-alfred.ai/blog/granola-pricing), [Efficient App review](https://efficient.app/apps/granola)) — it validates T.A.R.S.'s botless architecture but knows nothing about hiring. Fireflies transcribes 100+ languages with pt-BR UI and native Greenhouse/Lever sync; Otter is weaker on languages ([Fireflies comparison](https://fireflies.ai/blog/fireflies-vs-otter/)). The key gap: generic notetakers "don't know what a recruiting rubric is" and lack structured assessment ([Truffle roundup](https://www.hiretruffle.com/blog/ai-note-taking-tools-for-recruiters)).

**Brazil.** Local AI is concentrated in ATS-side screening: Gupy's Gaia ranks candidates and generates compatibility reports ([Gupy](https://www.gupy.io/blog/gaia-inteligencia-artificial-gupy)); InHire offers interview transcription/analysis inside its ATS ([InHire](https://www.inhire.com.br/produto/ia)). Live "copilots" in pt-BR exist mainly for **candidates**, not recruiters (e.g., [Final Round AI pt](https://www.finalroundai.com/pt/interview-copilot)). Exec-search-specific AI is sourcing/CRM (Loxo, Noon, Huntlo, Juicebox), not interview intelligence ([Noon roundup](https://www.noon.ai/blog/articles/56-executive-search-software), [Pin](https://www.pin.com/blog/ai-for-executive-search/)).

**Whitespace confirmed:** no tool combines botless capture + pt-BR-first + evidence-linked, human-judgment assessment for retained search. That is a real, defensible wedge — but the window is narrowing (Metaview courting exec search; Zoom/BrightHire scaling).

## 2. The winning launch wedge

In retained search the consultant is paid for **judgment delivered as a client-facing shortlist report**. Everything upstream (capture, coverage, notes) is input to that artifact. The week-one indispensable loop is:

**Prepare (CV+JD) → capture invisibly on any platform → notes/ratings in-flow → evidence-linked candidate report draft the same day.**

If T.A.R.S. turns a 2–3 hour post-interview write-up into a 20-minute review-and-approve session, in the consultant's own words, with quotes traceable to the transcript — it is indispensable. Nothing in the scan does this for pt-BR exec search. Conversely, **live suggestions are the least differentiated capability** (BrightHire does live guides; candidate copilots have commoditized the pattern) and the least valued by senior interviewers who already know how to interview. The hero screen just shipped (PR #3) is table stakes polish, not the wedge. Botless capture is a genuine moat versus Metaview/BrightHire for phone screens and client-platform calls — protect it.

## 3. Launch scope (4–6 weeks, Ella-internal)

| Tier | Capability | What breaks without it |
|---|---|---|
| **Must** | Reliable capture — fix the ~4.5-min STT rotation audio drop | Evidence-linked reports with holes in the evidence destroy trust on day one; the entire value proposition is "every conclusion traces to transcript" |
| **Must** | Recruiter notes + ratings surface, **AI ratings disabled** (human approval only) | Without recruiter judgment as input, output is generic AI summary — Fireflies at higher cost; also violates the product's own stated invariant |
| **Must** | Post-session report review + evidence-linked export (pt-BR, Ella-branded document) | No money artifact = no reason to use T.A.R.S. over Granola free tier |
| **Must** | Minimal session lifecycle: create session from CV+JD → interview → report | Reports need role context to assess against; orphan transcripts aren't assessments |
| **Must** | Basic auth/ownership | Colleagues' candidate PII in one shared pool is an LGPD incident waiting; unshippable to a second Ella user, let alone a second firm |
| **Should** | Competency coverage surface (simplified slice of interview-coverage-lab) | Valuable, but a skilled consultant covers competencies unaided in week one |
| **Should** | Consent/recording-notice capture logged per session | Cheap now; LGPD posture matters |
| **Should** | Transcript/speaker correction before report generation | Improves evidence quality; workaround is manual edit of report |
| **Should** | Windows capture (see disagreement in §5) | — |
| **Cut** | ATS/CRM integrations, English UI, billing/multi-tenant admin, live-suggestion enhancements beyond current, analytics dashboards, async AI screener, mobile | None of these block the wedge loop for ~5 internal users |

Integration guidance: cherry-pick report + notes slices from the labs; do not attempt to land the 49.5k-line integrated workspace in this window.

## 4. Commercial-readiness check (expensive to reverse later)

1. **Tenancy in the schema now.** Stamp `org_id` + `owner_id` on every session/transcript/report record even with one tenant. Retrofitting isolation across live candidate data is the classic migration nightmare.
2. **Separate report content from presentation.** Evidence-linked assessment as structured data; Ella branding as a template. A second firm is then a template, not a fork.
3. **Externalize strings and prompts.** pt-BR default, but hardcoded Portuguese in UI copy and Gemini prompts makes en/es expansion a rewrite. Prompt templates per locale from day one.
4. **Data ownership + deletion path.** Always-on local FLAC must become a per-org retention policy; design candidate-erasure (soft-delete cascade across audio/transcript/report) into the schema — LGPD applies fully to AI systems processing Brazilian residents' data, and ANPD has made AI governance a 2026–27 enforcement priority ([CMS guide](https://cms.law/en/int/expert-guides/ai-regulation-scanner/brazil), [AI Risk Aware](https://www.airiskaware.com/pt/insights/brazil-lgpd-ai-governance-2026)).
5. **Human-approved ratings as a versioned, auditable invariant.** Brazil's PL 2338 (EU-AI-Act-style, employment = high-risk) passed the Senate and sits in the Chamber ([Nathaly Calixto analysis](https://nathalycalixto.com/brazil-ai-regulation-complete-analysis-2026/)). "AI never rates; humans approve, with audit trail" is both compliance insurance and the sales pitch to other firms.

## 5. Disagreements and owner-only questions

**Disagreements.**
- **Windows-at-launch is the plan's biggest product risk.** Botless Windows system-audio capture is new platform engineering competing with the report loop for the same 4–6 weeks. If forced to choose, ship the Mac wedge complete and Windows with degraded capture (upload/loopback-device fallback) rather than a cross-platform app with no report.
- **Live-suggestion investment is misallocated.** The redesigned live screen polishes the commodity layer while the money artifact doesn't exist in the main app. Freeze live-screen work now.
- **AI-generated ratings must be disabled before any colleague touches it** — an internal launch that contradicts the product's core promise poisons word-of-mouth inside Ella.

**Questions only Deli can answer.**
1. What exactly does Ella send clients today (format, structure, language of the shortlist report)? The report exporter should clone it, not invent one.
2. Consent posture: will Ella disclose recording/AI-assistance to candidates? This decides whether we market "invisible capture" or "transparent copilot" — opposite positionings.
3. How many actual Windows users in the week-one cohort? If ≤2, does a loaner Mac buy the schedule back?
4. Do Ella's client contracts promise candidate-data deletion or confidentiality terms that constrain audio retention?
5. Is the eventual commercial model per-seat or per-search? It changes what usage telemetry we must start recording now.
