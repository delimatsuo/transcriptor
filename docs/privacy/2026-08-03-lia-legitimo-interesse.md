# Avaliação de Legítimo Interesse (LIA) — T.A.R.S.

**Data:** 2026-08-03
**Controladora:** Ella Executive Search ("Ella")
**Encarregado (DPO):** Deli Matsuo
**Escopo:** entrevistas de candidatos em processos seletivos executivos ativos.
**Status:** documento operacional de lançamento; não substitui a validação por assessoria jurídica brasileira.

## 1. Tratamento avaliado

Para conduzir uma busca executiva ativa, a Ella coleta e usa dados de currículos, descrição da vaga, entrevista, transcrição, notas da recrutadora e relatório de avaliação. O T.A.R.S. transcreve a entrevista e oferece assistência de IA ao vivo para preparar o relatório; a decisão e a aprovação do relatório permanecem humanas.

O produto não retém áudio bruto por padrão. A atribuição de falas é feita pelo roteamento de dois fluxos de áudio (microfone e áudio remoto), e **não** por vozprint, reconhecimento de voz ou identificação biométrica. A recrutadora não deve induzir o relato de dados sensíveis (por exemplo, saúde, filiação sindical ou opinião política); se surgirem incidentalmente, aplica-se minimização e, quando cabível, redação a pedido.

## 2. Base legal e teste de ponderação

A base legal para o tratamento estritamente ligado à busca em curso é o **legítimo interesse** da Ella, LGPD art. 7º, IX. Consentimento não é a base principal para esta etapa: a relação candidato–recrutadora tem assimetria e a retirada durante a seleção tornaria a finalidade operacional instável. Esta conclusão não abrange dados pessoais sensíveis, para os quais a Ella não usará legítimo interesse.

| Etapa do teste | Avaliação concreta |
|---|---|
| **Finalidade legítima** | Executar a busca solicitada pelo cliente: entender a experiência do candidato, apoiar a entrevista e produzir avaliação para a decisão de contratação. A finalidade é determinada, compatível com a atividade de recrutamento e limitada ao processo ativo. |
| **Necessidade** | Transcrição, notas e relatório reduzem erro e permitem revisão humana do que foi dito; o conteúdo é limitado à entrevista e à vaga. Áudio bruto não é persistido por padrão; não há biometria. Uma abordagem menos intrusiva não entrega o mesmo registro revisável da entrevista e da avaliação humana. |
| **Ponderação** | A expectativa razoável do candidato é ser avaliado no processo ao qual se candidatou, mas não ser gravado ou submetido a IA sem aviso. O risco residual é mitigado por aviso prévio, confirmação verbal registrada, canal de direitos, retenção curta, exclusão em cascata e revisão humana obrigatória. O tratamento não inicia sem o aviso. |

## 3. Expectativa, transparência e salvaguardas

Antes da captura, o candidato recebe e confirma o aviso: o que será registrado, transcrição, assistência de IA, finalidade do relatório, processamento no Google Cloud no exterior, prazo de retenção e canal de direitos. A objeção à gravação oferece caminho sem gravação. O registro da entrega do aviso integra a sessão.

Solicitações de eliminação devem excluir em cascata a sessão, transcrição, relatório, qualquer áudio e cópia de segurança, mantendo somente o registro de auditoria/tombstone necessário. A retenção aplicável é:

| Artefato | Política de retenção |
|---|---|
| Transcrição da entrevista | Excluir 90 dias após a entrega do relatório. |
| Notas da recrutadora | Arquivo restrito ao encerrar a busca; excluir em 2 anos. |
| Relatório de avaliação entregue ao cliente | Arquivo restrito; excluir em 5 anos após o encerramento da busca; acesso somente jurídico/DPO. |
| Currículo | Excluir ao encerrar a busca, salvo adesão específica ao banco de talentos. |

Ao fim do prazo, a regra é excluir; retirar o nome de uma transcrição não a torna anônima. Métricas verdadeiramente agregadas podem seguir tratamento separado. Deli Matsuo é o contato operacional do encarregado para exercício de direitos e escalonamento de privacidade.

## 4. Papéis das partes

A Ella é **controladora singular e independente** para a execução da busca: entrevistas, transcrições, notas, relatórios e banco de talentos. O cliente torna-se controlador singular e independente do relatório quando o recebe, respondendo por sua própria finalidade, retenção e atendimento de direitos. A finalidade de cada parte é relacionada, mas distinta; não se compartilha banco de talentos com clientes.

O contrato deve registrar: “As partes atuam como controladoras singulares e independentes: a Ella é controladora dos dados tratados na condução da busca (entrevistas, transcrições, notas e relatórios); o Cliente torna-se controlador independente dos dados contidos no relatório a partir de seu recebimento, respondendo por sua própria base legal, retenção e atendimento a direitos dos titulares, vedado o uso para finalidade diversa da decisão de contratação.”

Se o cliente determinar que a Ella opere exclusivamente dentro do ATS dele e sob suas instruções, essa atividade específica deve ser reavaliada como operação, não como a alocação acima.

## 5. Itens fora desta base legal

O banco de talentos após o encerramento da busca e o reuso de dados em outros mandatos exigem **consentimento específico**, com prazo informado (recomendado: até dois anos, renovável em novo contato) e opção de exclusão. Não são consequência automática de participar da busca atual.

## Fontes aprovadas

- `docs/superpowers/reviews/2026-08-03-launch-scope-panel/4-privacy.md`, linhas 3–17 e 25–54.
- `docs/superpowers/reviews/2026-08-03-launch-scope-panel/8-lgpd-retention-controller-research.md`, linhas 7–18 e 20–33.
