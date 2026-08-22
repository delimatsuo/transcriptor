# Evidência — prova ao vivo do canal do candidato (piloto-solo)

- **Gerado por:** `scripts/verify_live_system_audio.py` --with-restart-drill
- **Data (UTC):** 2026-08-22T01:58:48+00:00
- **Máquina:** macOS / 26.5.2 / 25F84 (arm64)
- **Commit:** `b99cb46ef5b3306dcc71d9cc0c591fd5749ac37a` — working tree: **SUJO — apenas 3 arquivo(s) não versionado(s) presente(s)**
- **Binário `tars-companion` exercitado:** compilado em 2026-08-22T01:36:56+00:00 (UTC)
- **Voz pt-BR usada:** Eddy (Portuguese (Brazil))
- **Backend:** uvicorn real em `127.0.0.1:8010`, `AUTH_BYPASS=true`, `HOST_AUDIO_CAPTURE_ENABLED` não definido
- **STT:** Google Speech-to-Text real (ADC verificada apenas por código de saída; nenhum token foi lido, impresso ou gravado)
- **Dependências Python:** `requests` e `websockets` já presentes no `.venv` — nada foi instalado

> ℹ Havia 3 arquivo(s) não versionado(s) na árvore, mas nenhum arquivo versionado modificado — o código exercitado corresponde ao commit acima.

## Resultado por fase

| # | Fase | Resultado | Detalhe |
|---|------|-----------|---------|
| 1 | Preflight ADC | **PASS** | credenciais padrão válidas (verificado por exit code) |
| 2 | Preflight porta | **PASS** | porta 8010 livre |
| 3 | Preflight voz pt-BR | **PASS** | voz 'Eddy (Portuguese (Brazil))' |
| 4 | Preflight binário companion | **PASS** | binário existente já atualizado |
| 5 | Backend up | **PASS** | /healthz respondendo em :8010 |
| 6 | Sessão criada | **PASS** | session_id=8bf4619352b34e8ea623f2f2a622aafa, stream_key presente (43 chars) |
| 7 | Chave inválida rejeitada | **PASS** | handshake rejeitado com HTTP 403 |
| 8 | Chave válida aceita (controle positivo) | **PASS** | conexão aceita e mantida aberta, encerrada limpa |
| 9 | Companion — captura de sistema ativa | **PASS** | ScreenCaptureKit iniciado |
| 10 | Canal do entrevistador enviado | **PASS** | WebSocket aberto com a chave válida; 3438 ms de fala real em quadros de 50 ms |
| 11 | Áudio do candidato reproduzido | **PASS** | frase dita 2x pela saída do sistema |
| 12 | Reinício do companion | **PASS** | capturou de novo com a mesma stream_key |
| 13 | Canal do entrevistador sustentado até o /stop | **PASS** | 637 frames entregues sem erro de socket |
| 14 | Sessão encerrada | **PASS** | transcription_complete=True |
| 15 | Segmento final rotulado 'Candidato' | **PASS** | palavras reconhecidas: ['candidato', 'experiencia', 'ingles', 'vendas'] |
| 16 | Segmento final rotulado 'Entrevistador' | **PASS** | palavras reconhecidas: ['entrevistador', 'pergunta'] |
| 17 | Sem duplicação entre falantes | **PASS** | nenhum texto final compartilhado |
| 18 | Fala pós-reinício transcrita | **PASS** | 1 segmento(s) 'Candidato' após o SIGKILL |

## Contagens observadas

- Frames injetados no canal do entrevistador (`source=microphone`): **637** quadros de 50 ms / 1600 B (1019.2 kB), dos quais **68** de fala real e o restante de silêncio de sustentação até o `/stop`
- Frames do canal do candidato (`source=system_audio`): produzidos pelo binário `tars-companion` via ScreenCaptureKit; o gateway não expõe um contador por fonte, então a prova desse canal é o segmento transcrito abaixo, não uma contagem
- Segmentos no transcript antes do `/stop`: **6**
- Segmentos no transcript depois do `/stop`: **6** (finais: **4**)
- `transcription_complete` devolvido pelo `/stop`: **True**

### Transcript final observado

| Falante | Texto |
|---------|-------|
| Entrevistador | Aqui fala o entrevistador fazendo uma pergunta. |
| Candidato | O candidato tem 10 anos de experiência em liderança de vendas e fala inglês fluente. |
| Candidato | O candidato tem 10 anos de experiência em liderança de vendas e fala inglês fluente. |
| Candidato | Esta frase vem depois do reinício da captura do candidato. |

## Pré-requisito de código (defeito encontrado por esta prova)

A primeira execução desta prova reprovou com **zero** segmentos do Candidato e expôs um defeito real no CLI do companion: `activeSources` não era lida depois dos `append`, e o ARC pode liberar uma variável local no seu **último uso** — não no fim do escopo. Em build de release isso derrubava o `SCStream` logo após o start, então a captura anunciava "active" e nenhum frame de áudio do sistema chegava ao gateway (silenciosamente: sem erro, sem queda de conexão). Diagnóstico: a mesma classe de captura entregou 126 frames em 6 s a um sink simples enquanto o binário entregava 0 no mesmo instante, na mesma máquina, com o mesmo áudio.

A correção (`withExtendedLifetime(activeSources)` no laço principal de `Sources/TarsCompanionCLI/main.swift`) está no commit `365fe20`. **Reproduzir esta prova exige um `tars-companion` compilado desse commit ou posterior**; binários anteriores falham nas fases do Candidato.

## Teto de alegação

> Comprova apenas: espinha de captura nativa funcionando ao vivo na máquina do proprietário (escopo piloto-solo). Não comprova: piloto G6, Windows, hospedagem, lançamento.

