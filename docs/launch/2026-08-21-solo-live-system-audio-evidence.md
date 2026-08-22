# Evidência — prova ao vivo do canal do candidato (piloto-solo)

- **Gerado por:** `scripts/verify_live_system_audio.py` --with-restart-drill
- **Data (UTC):** 2026-08-22T01:40:18+00:00
- **Máquina:** macOS / 26.5.2 / 25F84 (arm64)
- **Commit:** `216857665085d5b35b17c1e7dde115eb1efa16a4`
- **Voz pt-BR usada:** Eddy (Portuguese (Brazil)) pt_BR
- **Backend:** uvicorn real em `127.0.0.1:8010`, `AUTH_BYPASS=true`, `HOST_AUDIO_CAPTURE_ENABLED` não definido
- **STT:** Google Speech-to-Text real (ADC verificada apenas por código de saída; nenhum token foi lido, impresso ou gravado)
- **Dependências Python:** `requests` e `websockets` já presentes no `.venv` — nada foi instalado

## Resultado por fase

| # | Fase | Resultado | Detalhe |
|---|------|-----------|---------|
| 1 | Preflight ADC | **PASS** | credenciais padrão válidas (verificado por exit code) |
| 2 | Preflight porta | **PASS** | porta 8010 livre |
| 3 | Preflight voz pt-BR | **PASS** | voz 'Eddy (Portuguese (Brazil)) pt_BR' |
| 4 | Preflight binário companion | **PASS** | binário existente já atualizado |
| 5 | Backend up | **PASS** | /healthz respondendo em :8010 |
| 6 | Sessão criada | **PASS** | session_id=3eea2e80101a4165aa8a1c86d38faf77, stream_key presente (43 chars) |
| 7 | Chave inválida rejeitada | **PASS** | handshake rejeitado com HTTP 403 |
| 8 | Companion — captura de sistema ativa | **PASS** | ScreenCaptureKit iniciado |
| 9 | Canal do entrevistador enviado | **PASS** | WebSocket aberto com a chave válida; 3026 ms de fala real em quadros de 50 ms |
| 10 | Áudio do candidato reproduzido | **PASS** | frase dita 2x pela saída do sistema |
| 11 | Reinício do companion | **PASS** | capturou de novo com a mesma stream_key |
| 12 | Sessão encerrada | **PASS** | transcription_complete=True |
| 13 | Segmento final rotulado 'Candidato' | **PASS** | palavras reconhecidas: ['candidato', 'experiencia', 'ingles', 'vendas'] |
| 14 | Segmento final rotulado 'Entrevistador' | **PASS** | palavras reconhecidas: ['pergunta'] |
| 15 | Sem duplicação entre falantes | **PASS** | nenhum texto final compartilhado |
| 16 | Fala pós-reinício transcrita | **PASS** | 1 segmento(s) 'Candidato' após o SIGKILL |

## Contagens observadas

- Frames injetados no canal do entrevistador (`source=microphone`): **649** quadros de 50 ms / 1600 B (1038.4 kB), dos quais **60** de fala real e o restante de silêncio de sustentação até o `/stop`
- Frames do canal do candidato (`source=system_audio`): produzidos pelo binário `tars-companion` via ScreenCaptureKit; o gateway não expõe um contador por fonte, então a prova desse canal é o segmento transcrito abaixo, não uma contagem
- Segmentos no transcript antes do `/stop`: **6**
- Segmentos no transcript depois do `/stop`: **6** (finais: **4**)
- `transcription_complete` devolvido pelo `/stop`: **True**

### Transcript final observado

| Falante | Texto |
|---------|-------|
| Entrevistador | Aqui fala o entrevistado fazendo uma pergunta. |
| Candidato | O candidato tem 10 anos de experiência em liderança de vendas e fala inglês fluente. |
| Candidato | O candidato tem 10 anos de experiência em liderança de vendas e fala inglês fluente. |
| Candidato | Esta frase vem depois do início da captura do candidato. |

## Pré-requisito de código (defeito encontrado por esta prova)

A primeira execução desta prova reprovou com **zero** segmentos do Candidato e expôs um defeito real no CLI do companion: `activeSources` não era lida depois dos `append`, e o ARC pode liberar uma variável local no seu **último uso** — não no fim do escopo. Em build de release isso derrubava o `SCStream` logo após o start, então a captura anunciava "active" e nenhum frame de áudio do sistema chegava ao gateway (silenciosamente: sem erro, sem queda de conexão). Diagnóstico: a mesma classe de captura entregou 126 frames em 6 s a um sink simples enquanto o binário entregava 0 no mesmo instante, na mesma máquina, com o mesmo áudio.

A correção (`withExtendedLifetime(activeSources)` no laço principal de `Sources/TarsCompanionCLI/main.swift`) faz parte do mesmo commit desta evidência. **Reproduzir esta prova exige um `tars-companion` compilado desse commit ou posterior**; binários anteriores falham nas fases do Candidato.

## Teto de alegação

> Comprova apenas: espinha de captura nativa funcionando ao vivo na máquina do proprietário (escopo piloto-solo). Não comprova: piloto G6, Windows, hospedagem, lançamento.

