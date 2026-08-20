# Gate Windows da semana 1 — memo de decisão

**Status:** MODELO. Preencher somente após o spike VB-CABLE de 30 minutos na
máquina física de uma pessoa recrutadora Windows da coorte. Este arquivo não
contém uma decisão já tomada.

## Identificação do spike

| Campo | Evidência a preencher |
| --- | --- |
| Data e horário (fuso) | `PENDENTE` |
| Owner presente | `PENDENTE` |
| Recrutadora/recrutador Windows nomeado na primeira coorte | `PENDENTE` |
| Máquina testada (fabricante/modelo/asset tag) | `PENDENTE` |
| Windows (`winver`, edição e build) | `PENDENTE` |
| Navegador e app de reunião (Meet/Zoom) | `PENDENTE` |
| Headset usado | `PENDENTE` |
| Operador do spike | `PENDENTE` |
| Hash/revisão do aplicativo | `PENDENTE` |

## Resultados de validação

Preencher cada linha com `PASS`, `FAIL` ou `NÃO EXECUTADO`, além de um link ou
referência para a evidência (log, captura de tela sem PII ou anotação datada).

| Item | Resultado | Evidência / observação |
| --- | --- | --- |
| Windows 11 22H2 ou posterior confirmado | `PENDENTE` | |
| VB-CABLE instalado e máquina reiniciada | `PENDENTE` | |
| `CABLE Input` selecionado como saída do app de reunião | `PENDENTE` | |
| `CABLE Output` configurado com **Ouvir este dispositivo** nos fones | `PENDENTE` | |
| Preflight: `microphone` = PASS | `PENDENTE` | |
| Preflight: `system-audio` = PASS | `PENDENTE` | |
| Chamada Meet/Zoom de 30 minutos concluída | `PENDENTE` | |
| Rótulos `Entrevistador`/`Candidato` corretos | `PENDENTE` | |
| Nenhum aviso de silêncio | `PENDENTE` | |
| Limites de rotação de STT sem lacuna perceptível | `PENDENTE` | |
| Recrutadora/recrutador ouviu a outra ponta pelo headset | `PENDENTE` | |
| Log salvo sem conteúdo de conversa ou credenciais | `PENDENTE` | |

## Falhas, desvios e recuperação

Descrever cada falha, sua duração, a ação de recuperação e se ela é aceitável
para uma pessoa não engenheira. Se não houve falhas, registrar explicitamente
`Nenhuma observada`.

| Falha ou desvio | Duração/impacto | Recuperação aplicada | Aceitável sem engenheira/o? |
| --- | --- | --- | --- |
| `PENDENTE` | | | |

## Critério do gate (texto normativo da especificação §5)

> **Option A — Windows at launch:** spike clean → W5 lands packaging + checklist. Cost ≈ 1.5–2 of ~7 agent-weeks; squeeze absorbed by report/notes polish.
>
> **Option B — macOS launch W4 + Windows fast-follow with a committed date (~3 weeks post-launch).** Default if spike is dirty or no real Windows user exists in cohort.

Para aplicar o critério, o gate exige os dois pré-requisitos: (1) uma pessoa
recrutadora Windows real, identificada na coorte, e a respectiva máquina física;
(2) um spike VB-CABLE limpo nessa máquina. Roteamento frágil para pessoas não
engenheiras é evidência de spike sujo. Não inferir a opção a partir de uma
demonstração em máquina de desenvolvimento.

## Recomendação baseada na evidência

**Recomendação de quem executou o spike:** `PENDENTE`.

**Justificativa, citando os resultados e falhas acima:** `PENDENTE`.

Se a recomendação for Opção B, propor a data comprometida do fast-follow
Windows: `PENDENTE (AAAA-MM-DD; aproximadamente 3 semanas após o lançamento)`.

## DECISÃO DO OWNER

**Decisão (somente o owner marca):**

- [ ] Opção A — Windows no lançamento; Week 5 entrega empacotamento + checklist
  (custo aproximado: 1,5–2 agent-weeks).
- [ ] Opção B — lançamento macOS na Week 4; Windows fast-follow com data
  comprometida: `PENDENTE`.

**Nome do owner:** `PENDENTE`

**Data da decisão:** `PENDENTE`

**Observações ou condições da decisão:** `PENDENTE`
