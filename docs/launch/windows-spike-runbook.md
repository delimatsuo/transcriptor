# Runbook do spike Windows com VB-CABLE

**Status:** preparação para o gate da semana 1. Este documento não autoriza o
uso em entrevista real, não registra um resultado e não toma a decisão A/B.
O spike só é executado com o owner e uma recrutadora ou um recrutador Windows
nominalmente identificado na primeira coorte.

**Objetivo:** produzir evidência prática, na máquina física de uma pessoa que
realmente usará o produto, para o gate Windows. O caminho do spike usa o
aplicativo Python existente e o driver virtual VB-CABLE; não altera código nem
adiciona dependências.

## 1. Pré-requisitos

Antes de instalar qualquer coisa, registrar no memo do gate o nome da pessoa e
a máquina que será usada. Confirmar:

1. Windows 11. Execute `winver`: a versão deve ser 22H2 ou posterior. Windows
   10 não é suportado desde 2025-10-14.
2. Google Chrome ou Microsoft Edge, e Meet ou Zoom disponível para uma chamada
   de teste de 30 minutos.
3. Headset obrigatório. Não usar alto-falantes: o áudio remoto que retorna ao
   microfone faz as duas vozes parecerem `Candidato`.
4. Permissão local para instalar um driver de áudio e reiniciar a máquina.
5. Um clone local deste repositório e a credencial de serviço fornecida pelo
   owner. A chave JSON é secreta: não a adicionar ao Git, a um ticket ou ao
   memo do gate.

## 2. Instalar o VB-CABLE

1. Baixar e instalar o [VB-CABLE](https://vb-audio.com/Cable/) (driver
   gratuito).
2. Reiniciar o Windows quando o instalador solicitar.
3. Depois do reinício, confirmar que `CABLE Input` aparece como dispositivo de
   reprodução e `CABLE Output` como dispositivo de gravação.

## 3. Preparar o aplicativo local

1. Instalar Python 3.12 de python.org e selecionar **Add Python to PATH** no
   instalador.
2. Clonar o repositório e, no diretório raiz, executar:

   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

   A instalação não deve instalar `torch`, `torchaudio` ou `silero-vad`; esses
   pacotes foram removidos no Task 1 para manter esta preparação leve.

3. Copiar `.env.example` para `.env`. No arquivo local, preencher ou alterar:

   ```dotenv
   GOOGLE_APPLICATION_CREDENTIALS=C:\caminho\privado\para\service-account.json
   BLACKHOLE_DEVICE_NAME=CABLE Output
   MICROPHONE_DEVICE_NAME=
   ```

   `MICROPHONE_DEVICE_NAME` vazio usa o microfone padrão. Se a máquina tiver
   mais de um microfone, configurar o headset como padrão do sistema antes do
   spike. Não versionar `.env` nem a chave de serviço.

## 4. Rotear o áudio da chamada

1. Abrir **Configurações do Windows → Sistema → Som** e definir a saída do
   **aplicativo de reunião** como `CABLE Input`. Em versões que mostram isso no
   mixer por aplicativo, usar **Som → Mixer de volume** e mudar a saída de
   Chrome, Edge, Meet, Zoom ou Teams para `CABLE Input`, sem alterar outros
   aplicativos desnecessariamente.
2. Abrir **Painel de Controle → Som → Gravação → CABLE Output → Propriedades
   → Ouvir**.
3. Marcar **"Ouvir este dispositivo"** e escolher os fones de ouvido da pessoa
   recrutadora como dispositivo de reprodução.
4. Confirmar que a pessoa ainda ouve a outra ponta da chamada pelos fones. Esta
   etapa é indispensável: o aplicativo de reunião envia áudio para `CABLE
   Input`, e o encaminhamento de `CABLE Output` para os fones devolve essa
   audição sem misturar o microfone.

## 5. Validar antes e durante a chamada

1. Com áudio sendo reproduzido na chamada e a pessoa falando no headset, rodar
   o medidor do Task 8:

   ```powershell
   .venv\Scripts\python -m backend.scripts.preflight_audio
   ```

   Os resultados `microphone` e `system-audio` devem ambos ser `PASS`. Um
   `FAIL` interrompe o spike; corrigir o roteamento e repetir este passo antes
   de iniciar a chamada.
2. Rodar uma chamada real de Meet ou Zoom por 30 minutos com as duas pessoas e
   o aplicativo local em execução.
3. Registrar os quatro resultados no memo do gate:

   - os rótulos `Entrevistador` e `Candidato` correspondem à fonte de áudio;
   - nenhum aviso de silêncio aparece;
   - as transições de rotação de STT ocorrem sem lacunas perceptíveis;
   - a pessoa recrutadora continua ouvindo a outra ponta pelo headset.

4. Preservar o log da execução e anotar observações/falhas no memo. Não incluir
   conteúdo de conversa ou credenciais no memo.

## 6. Solução de problemas

| Sintoma | Causa provável | Correção antes de continuar |
| --- | --- | --- |
| Não há áudio do sistema / `system-audio` falha | A saída do aplicativo de reunião não está em `CABLE Input`. | Voltar ao Mixer de volume/Configurações de Som, definir a saída do aplicativo de reunião como `CABLE Input` e repetir o preflight. |
| A pessoa recrutadora não escuta a outra ponta | **Ouvir este dispositivo** está desmarcado ou aponta para o dispositivo errado. | Em `CABLE Output → Propriedades → Ouvir`, habilitar **Ouvir este dispositivo** e selecionar os fones. |
| As duas vozes aparecem como `Candidato` | O microfone também está captando o áudio remoto, normalmente por alto-falantes ou microfone errado. | Usar headset e confirmar o microfone do headset como padrão/selecionado; repetir o preflight e a validação de rótulos. |

## 7. Encerramento do spike

Preencher `docs/launch/2026-08-0X-windows-gate-decision.md` com a máquina, a
versão do Windows, os resultados de cada item, as falhas e a recomendação. A
recomendação não é a decisão: o owner escolhe formalmente a Opção A ou B após
ver a evidência.
