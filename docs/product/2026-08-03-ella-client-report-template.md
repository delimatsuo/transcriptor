# Ella Client Report — Structure Template

**Date:** 2026-08-03
**Source:** Derived from a real Ella client deliverable supplied by the owner on 2026-08-03. The original sample contains real candidate and client PII and is deliberately NOT committed to this repository (per README data policy). This document captures its structure, register, and content rules; the W3 report exporter clones THIS format.

## What the real deliverable is

A dense two-block prose narrative in pt-BR, written in the consultant's first-person voice. It is **not** a rubric: no numeric ratings, no per-competency scores, no "Recomendado / Não Recomendado" verdict labels. The recommendation is a **process recommendation** (advance/don't advance, with named next steps), not a hire verdict.

## Block 1 — Trajetória (narrative career biography)

One dense paragraph. Chronological-ish career story with heavy quantification. Content rules observed in the sample:

- Opens with a one-line identity: name, professional archetype, years of experience, current role + scope ("liderando [unidade] com [N] pessoas").
- Every claim is concrete and quantified where possible: team sizes, revenue figures and growth ("chegou quando a empresa faturava R$X, acompanhou o crescimento para R$Y"), timeframes ("em ~1,5 ano"), counts ("vendida em white label para N empresas").
- Career-defining distinctions called out explicitly (rare titles and their rarity, awards, notable clients).
- Signature achievements narrated as cause→effect ("liderou a migração completa de X para Y, reduzindo o TCO pela metade, utilizando [método]").
- Closes with education (degrees + institutions), notable programs, awards, teaching/speaking.

## Block 2 — Avaliação (consultant's assessment)

One paragraph, first-person consultant voice. Ordered movement observed in the sample:

1. **Overall read** — one sentence: strength of profile + authenticity of motivation.
2. **Motivation coherence** — why the candidate's current dissatisfaction/stated drivers are (or aren't) coherent with what the target role offers. Names the current-role friction specifically.
3. **Direct parallel** — which past experience is the closest analog to the target challenge, and why ("A passagem por X é o paralelo mais direto: [shared characteristics]").
4. **Target-specific advantages** — experiences that de-risk the specific client context (sector, regulatory environment, stage).
5. **Honest epistemic limits** — what the consultant could NOT assess deeply, stated plainly in first person ("não consegui avaliar com profundidade se..."), including why it matters for this specific client ("dado [client-specific bar]"). This is a load-bearing feature of the format, not a weakness — it maps exactly to the product's "insufficient evidence" posture.
6. **Pontos de atenção** — concrete risks (e.g., short tenure), stated without euphemism.
7. **Process recommendation** — a named next step with named people ("Recomendo avançar para entrevista com [nome] e, em seguida, [avaliação técnica] com [nome], ou vice-versa").

## Register and style rules

- pt-BR throughout. Consultant first person in Block 2 ("não consegui avaliar"); third person for the candidate.
- Dense continuous prose. No bullets, no headers, no tables in the deliverable itself.
- Zero hedging filler; every sentence carries a fact or a judgment.
- Numbers stay specific (R$ figures, headcounts, durations) — vagueness reads as weakness in this genre.
- Candor over polish: limits and risks are stated directly.

## Implications for the product (W3 exporter)

1. **Two artifacts, not one.** The internal review screen may show the AI's structured draft aids (competency observations, evidence links, and — hard-gated per owner decision #5 — draft ratings). The **client-facing export is this narrative format only**; ratings and rubric structure never appear in it.
2. The existing `INTERVIEW_REPORT_PROMPT` (1–5 ratings + Recomendado/Não Recomendado) does **not** match Ella's real deliverable and must be restructured for the export path: Block 1 sourced from CV + transcript facts; Block 2 sourced from transcript evidence + recruiter notes (the note chips' concern/strength/follow-up entries feed movements 5–6 directly).
3. Movement 5 (epistemic limits) should be generated from what the interview did NOT establish — this is where "insufficient evidence" lives in client-facing language.
4. Names in movement 7 (client-side interviewers/next steps) come from the session's briefing context, entered by the recruiter — never invented by the model.
