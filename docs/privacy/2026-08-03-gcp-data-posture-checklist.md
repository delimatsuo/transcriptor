# Checklist de postura de dados no Google Cloud — pré-entrevista real

**Data:** 2026-08-03
**Projeto de produção:** `transcriptor-490222`.
**Regra de evidência:** preencher cada linha com saída, captura de tela ou link de contrato datado antes da primeira entrevista real. Um item `PENDENTE` bloqueia a respectiva afirmação de privacidade; os itens de cache, abuso, logging de STT e CDPA/SCCs são pré-requisitos de lançamento.

## 1. Contexto de verificação

Use sempre o identificador do projeto de produção, não o de desenvolvimento:

```bash
export PROD_PROJECT_ID='SUBSTITUIR_PELO_ID_DO_PROJETO_DE_PRODUCAO'
gcloud config set project "$PROD_PROJECT_ID"
```

Não use endpoint global para Vertex. A orientação de residência é `southamerica-east1` primeiro; quando inviável, UE é a alternativa preferível. A disponibilidade de STT streaming em São Paulo deve ser confirmada durante a configuração; independentemente disso, trate a transferência internacional como existente e mantenha o mecanismo contratual aplicável.

## 2. Verificações automatizáveis

| Item | Comando (executar uma vez) | Critério | Estado / evidência |
|---|---|---|---|
| Conta e projeto ativos | `gcloud config list --format='text(core.project,core.account,core.disable_usage_reporting)'` | Conta corporativa e ID do projeto de produção aparecem. | **VERIFICADO externamente, 2026-08-03:** ADC funcional pós-reautenticação para `deli@ellaexecutivesearch.com`, projeto `transcriptor-490222`. A credencial do CLI `gcloud` expira separadamente do ADC; antes de executar estes comandos, o owner deve rodar `gcloud auth login`. |
| Existência e estado do projeto | `gcloud projects describe "$PROD_PROJECT_ID" --format='yaml(projectId,projectNumber,lifecycleState)'` | `lifecycleState: ACTIVE` e ID confere com o projeto de produção. | **VERIFICADO externamente, 2026-08-03:** projeto `transcriptor-490222` usado para a verificação ADC; anexar saída do describe em evidência operacional futura. |
| Acesso IAM para operação | `gcloud projects get-iam-policy "$PROD_PROJECT_ID" --format='table(bindings.role)'` | Papéis permitem operar os serviços e são revisados pelo owner; não registrar membros/endereços nesta tabela. | **VERIFICADO externamente, 2026-08-03:** zero bindings para `allUsers` ou `allAuthenticatedUsers`. |
| APIs necessárias | `gcloud services list --enabled --project "$PROD_PROJECT_ID" --format='value(config.name)'` | Confirmar, no mínimo, `speech.googleapis.com`, `aiplatform.googleapis.com`, `firestore.googleapis.com` e `storage.googleapis.com`. | **VERIFICADO externamente, 2026-08-03:** `aiplatform`, `firestore`, `run`, `speech`, `storage` e `bigquerystorage`; conjunto esperado, sem API inesperada habilitada. |
| Dados Firestore/GCS pós-purge | `gcloud firestore databases list --project="$PROD_PROJECT_ID"` e `gcloud storage ls "gs://$PROD_PROJECT_ID-tars"` | Nenhum dado candidato legado permanece. | **VERIFICADO externamente, 2026-08-03:** plano de dados vazio após purge; evidência em `docs/current-state/2026-08-03-legacy-data-purge-evidence.md`. |
| Bucket `transcriptor-490222-tars` | `gcloud storage buckets describe gs://transcriptor-490222-tars` | PAP aplicado; controles de acesso documentados. | **VERIFICADO externamente, 2026-08-03:** `publicAccessPrevention=enforced`. Uniform bucket-level access está desligado; aceitável enquanto PAP estiver enforced, mas candidato de hardening. |
| Cloud Run legado | `gcloud run services list --project="$PROD_PROJECT_ID" --region=us-central1` | Não há serviço publicamente invocável. | **VERIFICADO externamente, 2026-08-03:** `tars-backend-staging`, `us-central1`, última atualização 2026-03-16; ingress `ALL`, porém sem bindings IAM — não invocável. Recomendação não bloqueante ao owner: apagar o serviço obsoleto e suas imagens de container. |

## 3. Verificações de console — owner obrigatório

| Item | Caminho exato no console | Critério | Estado / evidência |
|---|---|---|---|
| Cache de prompts do Vertex/Gemini | Verificação via API (mais forte que captura de tela): `GET https://us-central1-aiplatform.googleapis.com/v1/projects/transcriptor-490222/cacheConfig` | Cache de prompts desativado no projeto de produção; não aceitar a retenção padrão de até 24 h. | **VERIFICADO, 2026-08-03:** a API retornou `{"name": "projects/33726443105/cacheConfig", "disableCache": true}` (HTTP 200, sessão autenticada ADC do owner). Cache de prompts já estava desativado no projeto de produção. |
| Monitoramento de abuso / logging de prompts | Console → selecionar projeto de produção → **Vertex AI** → **Generative AI** → **Data governance** → controle de *abuse monitoring* / retenção de prompts. Se o console direcionar ao formulário de opt-out por conta de faturamento, completar esse formulário e registrar a confirmação. | Opt-out de logging de prompts para monitoramento de abuso está efetivo para o projeto/conta de faturamento. | **SOLICITADO, 2026-08-03:** owner submeteu o formulário de exceção para o projeto `33726443105` / `transcriptor-490222`, declarou domínio sensível de contratação/emprego e confirmou monitoramento humano. Decisão do Google pendente (estimativa: ~2 semanas); o opt-out não está efetivo até aprovação. |
| Logging de dados do Speech-to-Text | Console → selecionar projeto de produção → **Speech-to-Text** → **Data logging**. | Não aderir ao programa de data logging com desconto. Para streaming sem adesão, dados do cliente são processados em memória; metadados de requisição podem ser temporariamente registrados. | **VERIFICADO, 2026-08-03:** owner conferiu no console do projeto `transcriptor-490222`: "Data logging is disabled for this project for Google Cloud Speech API" (nunca aderiu; botão oferecia *Enable*). O próprio console registra que a API **v2** — a única usada pelo T.A.R.S. — não suporta data logging. |
| CDPA e Cláusulas Contratuais-Padrão brasileiras | Console → selecionar a conta de faturamento/organização → **Billing** → **Account management** → contratos; conferir também o contrato Google Cloud aplicável e o [adendo BR C2P](https://cloud.google.com/sccs/br-c2p). | O acordo da Ella incorpora o Cloud Data Processing Addendum e as SCCs brasileiras antes de qualquer entrevista real com transferência internacional. | **PENDENTE (owner):** link/ID do contrato ou confirmação jurídica datada. |
| Região e endpoint do Vertex | Código-fonte: `backend/llm/gemini.py:26` (`aiplatform.init(project=...)` sem `location`). | `southamerica-east1` quando disponível; jamais endpoint `global` para este fluxo. Se outra região for usada, registrar a transferência e a justificativa. | **DOCUMENTADO, 2026-08-03:** o cliente inicializa sem `location` → região padrão `us-central1`. Não é endpoint `global`, mas também não é São Paulo. Transferência internacional existe e depende do mecanismo CDPA/SCCs (linha acima). **Candidato W2:** testar disponibilidade do `gemini-2.5-flash` em `southamerica-east1` e passar `location` explícito. |
| Região/endpoint do STT streaming | Código-fonte: `backend/config.py:53-55` (`stt_location`, padrão `us`; comentário registra que `chirp_3` exige `us` ou `eu`). | Região e endpoint efetivos documentados; se São Paulo não for suportado, registrar a transferência internacional e manter CDPA/SCCs. | **DOCUMENTADO, 2026-08-03:** região efetiva `us` (endpoint `us-speech.googleapis.com`, `backend/stt/google_stt.py:39-43`). O modelo `chirp_3` não está disponível em São Paulo — residência brasileira para STT é inviável hoje; transferência internacional registrada, coberta pelo mecanismo contratual (pendente só a confirmação CDPA/SCCs). |

## 4. Resultado de liberação

Estado em 2026-08-03 (fim do dia): cache do Vertex **desativado (verificado)**; data logging do STT **não aderido (verificado)**; regiões **documentadas**; plano de dados **vazio pós-purge**. Restam dois itens:

1. **CDPA + SCCs brasileiras** — `PENDENTE`: confirmação do owner de que não existe contrato Google customizado (conta padrão ⇒ CDPA incorporado por padrão). **Bloqueante** para a primeira entrevista real.
2. **Opt-out de monitoramento de abuso** — `SOLICITADO`, decisão do Google em ~2 semanas. A efetivação não depende da Ella. **Decisão do owner registrar aqui:** aceitar o estado "solicitado-pendente" para as entrevistas internas da semana 1 (racional: o logging de abuso só ocorre em prompts *sinalizados* por classificadores; o cache — retenção padrão — já está desativado; candidatos são avisados; conteúdo coberto pelo CDPA), ou adiar entrevistas reais até a aprovação. A recomendação técnica é aceitar o estado pendente; a decisão é do owner.

## Fontes aprovadas

- `docs/superpowers/reviews/2026-08-03-launch-scope-panel/4-privacy.md`, linhas 13–23 e 44–54.
- `docs/superpowers/reviews/2026-08-03-launch-scope-panel/8-lgpd-retention-controller-research.md`, linhas 7–18 e 20–33.
