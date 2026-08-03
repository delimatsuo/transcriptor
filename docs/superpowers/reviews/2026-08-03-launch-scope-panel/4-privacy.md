# T.A.R.S. — Security, Privacy & Compliance Launch Report

**Scope note:** This is compliance analysis for the owner's review, not legal advice. Ella should validate the legal-basis and transfer conclusions with Brazilian counsel before launch.

## 1. LGPD analysis for this exact processing

**Legal basis.** For CVs, job descriptions, transcripts, and evaluation of candidates within an active search, *legítimo interesse* (Art. 7, IX) is the defensible primary basis, and practitioner guidance treats processing "strictly linked to the ongoing selection" as fitting it ([Conjur](https://www.conjur.com.br/2020-set-24/pratica-trabalhista-adequacao-lgpd-recrutamento-selecao-candidatos-emprego/), [FGV RH guide](https://portal.fgv.br/sites/default/files/uploads/recursos_humanos.pdf)). Consent is the wrong primary basis here (power asymmetry, revocability mid-search), but IS required for extras: talent-pool retention after the search ends, and reuse across mandates ([Rücker Curi](https://www.curi.adv.br/aplicacao-da-lgpd-nos-processos-de-recrutamento-e-selecao/)). Legítimo interesse requires a documented balancing test (LIA) — write it once, before launch.

**Sensitive data.** A voice recording is ordinary personal data; it becomes *sensitive biometric* data only if voice is used to identify the person ([Confidata](https://confidata.com.br/blog/lgpd-dados-biometricos-reconhecimento-facial-digital), [Be Compliance](https://becompliance.com/lgpd-e-o-uso-de-imagem-e-voz-sao-dados-pessoais-sensiveis-entenda-o-risco/)). **Design constraint: never do voiceprint-based speaker ID.** The existing dual-stream (mic vs. remote) attribution keeps T.A.R.S. out of biometrics. Separately, interviews *incidentally* capture sensitive data (health, union, political opinion — Art. 5, II), for which legítimo interesse is unavailable (Art. 11). Mitigations: recruiter guidance not to elicit it, short retention, and redaction on request.

**Recording legality.** STJ and STF case law holds that recording by a conversation participant is lawful ("gravação clandestina" ≠ unlawful interception), reaffirmed with general repercussion ([STF](https://noticias.stf.jus.br/postsnoticias/stf-reconhece-repercussao-geral-e-reafirma-ser-possivel-aproveitar-gravacao-como-prova/), [STJ analysis](https://meusitejuridico.editorajuspodivm.com.br/2018/04/02/stj-e-licita-gravacao-de-conversa-feita-pelo-destinatario-de-solicitacao-de-vantagem-indevida/)). But that is evidence law, not LGPD: covert recording would still violate LGPD transparency/purpose principles. **Tell candidates before recording — this is an LGPD requirement in practice, not a wiretap-law one.**

**Disclosure & rights.** Candidates must be told, before capture: what is recorded, purposes (transcription, live AI assistance, assessment report), that Google Cloud processes data abroad, retention period, and the contact channel for rights (Art. 9 / Art. 18). Deletion (eliminação) must actually work at launch — a request must cascade across Firestore session records, transcripts, reports, any audio, and backups.

**International transfer.** ANPD's Resolution CD/ANPD 19/2024 governs transfers; its grace period ended 23 Aug 2025, so a valid mechanism is mandatory now ([Mayer Brown](https://www.mayerbrown.com/en/insights/publications/2025/08/end-of-grace-period-implementation-of-brazils-standard-contractual-clauses-in-international-transfers-of-personal-data), [ANPD](https://www.gov.br/anpd/pt-br/assuntos/assuntos-internacionais/transferencia-internacional-de-dados)). The **US has no adequacy decision; the EU does** (mutual recognition, Jan 2026) ([Confidata guide](https://confidata.com.br/blog/transferencia-internacional-dados-guia-completo), [CMT](https://cmtadv.com.br/pt/transferencia-internacional-de-dados-pessoais-o-que-muda-na-relacao-brasil-uniao-europeia/)). Google publishes Brazil-specific SCCs incorporated via the Cloud Data Processing Addendum ([Google Cloud BR SCCs](https://cloud.google.com/sccs/br-c2p)) — **verify Ella's agreement actually incorporates the CDPA + BR SCCs**; prefer EU or São Paulo regions to strengthen the story.

**RIPD (DPIA).** Expected. AI-assisted candidate evaluation is squarely in ANPD's "high risk to fundamental rights" territory ([ANPD RIPD page](https://www.gov.br/anpd/pt-br/canais_atendimento/agente-de-tratamento/relatorio-de-impacto-a-protecao-de-dados-pessoais-ripd)). Write it before launch; it also forces the LIA and retention decisions. Art. 20 (review of automated decisions) is why ADR 0001's human-approval rule is load-bearing.

## 2. Google Cloud data posture

- **STT:** with data logging off (the default), "Google processes it in memory and does not store any customer data" for streaming requests; only request metadata is temporarily logged ([STT data-usage FAQ](https://docs.cloud.google.com/speech-to-text/docs/data-usage-faq)). This claim holds. **Never opt into the discounted data-logging program.**
- **Vertex/Gemini:** customer data is not used for training, but prompts are **cached up to 24h by default** — disable caching at project level, and opt out of abuse-monitoring prompt logging (form/invoiced billing) to reach effective zero retention ([Gemini ZDR docs](https://ai.google.dev/gemini-api/docs/zdr), [Vertex data governance](https://cloud.google.com/vertex-ai/generative-ai/docs/data-governance)). The dev project already does this; **replicate both opt-outs in the production project before first real interview.**
- **Regions:** Generative AI on Vertex is available in **southamerica-east1** with data-residency commitments; the *global* endpoint gives no residency control — use regional ([Vertex data residency](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/data-residency)). STT regional endpoints are documented for US/EU boundaries; São Paulo streaming STT availability is unclear ([STT endpoints](https://docs.cloud.google.com/speech-to-text/docs/v1/endpoints), [locations](https://docs.cloud.google.com/speech-to-text/docs/locations)) — query the Locations API during build. Plan on audio leaving Brazil, so the SCC/CDPA mechanism is needed regardless.

## 3. The launch bar (Ella-internal changes nothing for candidates)

**Launch-blocking (do not run a real interview until fixed):**
1. **FLAC-always-written** — directly violates ADR 0001 and minimization (Art. 6, III); creates the exact artifact deletion requests will trip over.
2. **No auth / no ownership enforcement** — Art. 46 security duty; even 5 internal users need identity, because audit and DSAR answers depend on "who accessed what."
3. **Unenforced retention + no working deletion** — Art. 18 deletion must function end-to-end at launch, even if triggered manually by an admin runbook.
4. **16 legacy records + 4 PDFs** — unresolved basis means they are unlawful holdings today. Cheapest cure: delete before launch unless the owner confirms a basis; keeping them "to migrate later" is the trap.
5. **No candidate disclosure flow** — minimum viable: (a) pre-interview email with a short privacy notice (recording, AI use, Google processing abroad, retention, rights contact); (b) verbal confirmation at interview start, captured in the transcript; (c) a no-recording fallback path if the candidate objects; (d) notice-given flag stored on the session.

**90-day-fixable:** AI-direct ratings are acceptable at launch **only** behind a mandatory human-approval gate with "AI-generated draft" labeling (Art. 20 mitigation); evidence-link polish, DSAR self-service, full audit-log coverage, and the formal RIPD document can mature over 90 days (draft RIPD still needed at launch).

## 4. Cheap now, brutal to retrofit (decide in the next 6 weeks)

1. **Tenancy:** server-derived `org_id` + `owner_id` on every record and every storage path now, even with one tenant. Re-keying Firestore later is the classic disaster.
2. **Deletion-by-design:** one deletion function keyed by candidate/session that cascades everywhere, leaving a tombstone audit entry; retention as enforced infrastructure (Firestore TTL policies), not config.
3. **Audit log shape:** append-only, with actor, org, action, object, purpose, timestamp from day one — you cannot reconstruct history you never wrote.
4. **Residency & keys:** commit to southamerica-east1-first (fallback EU, never global endpoint); decide Google-managed keys vs. CMEK posture now — implementing CMEK later is feasible, but *migrating regions* later means moving every transcript and re-papering transfers.
5. **No-biometrics rule:** codify "speaker attribution via stream routing only" in the ADRs so a future diarization feature doesn't silently create sensitive-data processing.

## 5. Launch scope (privacy/security view)

| Must | Should | Cut |
|---|---|---|
| Auth + server-side ownership | southamerica-east1 residency | Consent-management platform |
| Remove FLAC default (in-memory only) | Audit log v1 (shape above) | Candidate self-service portal |
| Enforced retention + delete cascade | Breach-response runbook | CMEK implementation |
| Candidate notice flow + LIA doc | Encarregado (DPO) named & published | Certifications (SOC2 etc.) |
| Purge/regularize 16 legacy records | Full RIPD document (draft at launch) | Cross-search talent pool |
| CDPA + BR SCCs verified; prod cache/abuse-logging opt-outs | Recruiter training on sensitive topics | Voiceprint/diarization features |
| Human-approval gate on assessments | | |

**Biggest risk:** an access or deletion failure involving an *executive* candidate — the candidate pool is senior, identifiable, and litigious, and Ella's product is discretion. A single "you still have my interview recording" incident is existential; note the prototype in its current state (always-on FLAC, no notice, no auth) would already be non-compliant if pointed at a real candidate today.

**Owner-only questions:** (1) Who is the encarregado/DPO of record? (2) Retention period for transcripts vs. reports — what does the search mandate actually require? (3) Delete or regularize the 16 legacy sessions and 4 PDFs — were those candidates ever informed? (4) Is Brazil-only processing worth cost/latency, or is EU/US with SCCs acceptable? (5) Controller structure: is Ella sole controller, or co-controller with hiring clients who receive reports — this changes the notice and the contracts.
