# Evidência — prova ao vivo do canal do candidato (piloto-solo)

- **Gerado por:** `scripts/verify_live_system_audio.py --with-restart-drill`
- **Data (UTC):** 2026-09-05T02:31:50+00:00
- **Máquina:** macOS / 26.6.2 / 25G83 (arm64)
- **Commit:** `913fff80409c31a0745e240ac455094b0717bad9` — working tree: limpo
- **App assinado exercitado:** `/Volumes/Extreme Pro/MYPROJECTS/Transcriptor/dist/TarsCompanion.app`
- **Voz pt-BR usada:** Eddy (Portuguese (Brazil))
- **Backend:** uvicorn real em `127.0.0.1:8010`, `AUTH_BYPASS=true`, `HOST_AUDIO_CAPTURE_ENABLED` não definido
- **STT:** Google Speech-to-Text real (ADC verificada apenas por código de saída; nenhum token foi lido, impresso ou gravado)
- **Dependências Python:** `requests` e `websockets` já presentes no `.venv` — nada foi instalado

## Resultado por fase

| # | Fase | Resultado | Detalhe |
|---|------|-----------|---------|
| 1 | Preflight proveniência da árvore | **PASS** | producer template |
| 2 | Preflight ADC | **PASS** | producer template |
| 3 | Preflight porta | **PASS** | producer template |
| 4 | Preflight voz pt-BR | **PASS** | producer template |
| 5 | Preflight app assinado | **PASS** | producer template |
| 6 | Preflight proveniência/assinatura do app | **PASS** | producer template |
| 7 | Backend up | **PASS** | producer template |
| 8 | Sessão criada | **PASS** | producer template |
| 9 | Chave inválida rejeitada | **PASS** | producer template |
| 10 | Chave válida aceita (controle positivo) | **PASS** | producer template |
| 11 | Companion — estado da captura Process Tap | **PASS** | producer template |
| 12 | Canal do entrevistador enviado | **PASS** | producer template |
| 13 | Áudio do candidato reproduzido | **PASS** | producer template |
| 14 | Reinício do companion | **PASS** | producer template |
| 15 | Canal do entrevistador sustentado até o /stop | **PASS** | producer template |
| 16 | Companion — fatos positivos antes da parada | **PASS** | producer template |
| 17 | Companion — cleanup após a parada | **PASS** | producer template |
| 18 | Sessão encerrada | **PASS** | producer template |
| 19 | Segmento final rotulado 'Candidato' | **PASS** | producer template |
| 20 | Segmento final rotulado 'Entrevistador' | **PASS** | producer template |
| 21 | Sem duplicação entre falantes | **PASS** | producer template |
| 22 | Fala pós-reinício transcrita | **PASS** | producer template |
| 23 | Documento de evidência secret-safe | **PASS** | producer template |

## Contagens observadas

- Frames injetados no canal do entrevistador (`source=microphone`): **655** quadros de 50 ms / 1600 B (1048.0 kB), dos quais **78** de fala real e o restante de silêncio de sustentação até o `/stop`
- Frames do canal do candidato (`source=system_audio`): produzidos pelo app menu-bar assinado via Process Tap; o gateway não expõe um contador por fonte, então a prova desse canal é o segmento transcrito abaixo, não uma contagem
- Segmentos no transcript antes do `/stop`: **6**
- Segmentos no transcript depois do `/stop`: **6** (finais: **4**)
- `transcription_complete` devolvido pelo `/stop`: **True**

### Transcript final observado

_Texto do transcript intencionalmente omitido da evidência por segurança; contagens, rotulagem de falantes e validação tipada comprovadas acima._

## Pré-requisito de código (defeito encontrado por esta prova)

A primeira execução desta prova reprovou com **zero** segmentos do Candidato e expôs um defeito histórico no companion: `activeSources` não era lida depois dos `append`, e o ARC pode liberar uma variável local no seu **último uso** — não no fim do escopo. Em build de release isso derrubava o `SCStream` logo após o start, então a captura anunciava "active" e nenhum frame de áudio do sistema chegava ao gateway (silenciosamente: sem erro, sem queda de conexão). Diagnóstico: a mesma classe de captura entregou 126 frames em 6 s a um sink simples enquanto o binário entregava 0 no mesmo instante, na mesma máquina, com o mesmo áudio.

A correção (`withExtendedLifetime(activeSources)` no laço principal) está no commit `365fe20`; reproduzir esta prova exige o app assinado produzido pelo modo Task 11. binários anteriores falham nas fases do Candidato.

## Teto de alegação

> Comprova apenas: espinha de captura nativa funcionando ao vivo na máquina do proprietário (escopo piloto-solo). Não comprova: piloto G6, Windows, hospedagem, lançamento.

