# T.A.R.S. Recruiter Pilot Onboarding Package

**Version:** 1.0.0 (Launch Pilot Edition)  
**Target Audience:** Executive Search Recruiters & Interviewers (macOS — piloto atual; Windows indisponível nesta fase)  
**Governing Architecture:** ADR 0003 (`docs/architecture/0003-native-capture-launch-boundary.md`)  
**Cockpit Web Interface:** `http://localhost:3000` (or company staging URL)

---

## 1. Bem-vindo ao T.A.R.S. (Transcriptor)

O **T.A.R.S.** é o seu copiloto de inteligência em tempo real para entrevistas executivas. Ele transcreve a conversa com precisão e diarização automática (**Entrevistador** e **Candidato**), identifica lacunas de áudio com transparência e fornece sugestões contextuais de perguntas para aprofundamento das competências do candidato.

### ✨ Principais Vantagens do Novo Modo Nativo:
- **Zero Configuração de Drivers:** Você **NÃO** precisa instalar BlackHole, VB-CABLE nem alterar Ajustes de Áudio e MIDI.
- **Detecção Automática:** O áudio do seu microfone e o áudio da chamada (Google Meet, Zoom, Microsoft Teams) são capturados nativamente com isolamento total.
- **Transparência de Cobertura:** Se houver oscilação de rede ou queda de conexão, o sistema sinaliza exatamente o intervalo de áudio afetado na linha do tempo.

---

## 2. Pré-requisitos & Equipamentos

1. **Computador:**
   - **macOS:** macOS 13.0 (Ventura) ou superior (Apple Silicon M1/M2/M3/M4 ou Intel).
   - **Windows:** indisponível nesta fase do piloto (o companion Windows é um esqueleto sem captura real).
2. **Headset ou Fones de Ouvido:** **Obrigatório.** O uso de fones evita que a voz do candidato ecoe no seu microfone físico, garantindo que os rótulos de quem está falando fiquem 100% corretos.
3. **Navegador:** Google Chrome, Microsoft Edge ou Safari atualizados.

---

## 3. Passo a Passo: Preparação Rápida (3 Minutos)

### Passo 1: Instalar o Aplicativo Menu-Bar TarsCompanion
1. Obtenha o aplicativo assinado `dist/TarsCompanion.app` e mova-o para a pasta `/Applications` (ou execute-o diretamente).
2. Na primeira inicialização, o ícone do **T.A.R.S.** aparecerá discretamente na sua barra de menus do macOS.
3. O app está assinado com Developer ID oficial (`Travel Advisory LLC`) e possui runtime protegido.

---

### Passo 2: Conceder Permissão de Gravação de Tela e Áudio do Sistema
Para capturar o áudio das reuniões (Google Meet, Teams, Zoom) sem necessidade de drivers virtuais:
1. Abra **Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema**.
2. Garanta que o **TarsCompanion** esteja habilitado na lista.
3. Na primeira execução do app, o próprio macOS solicitará essa permissão; basta clicar em **Permitir**.

---

### Passo 3: Acessar o Cockpit Web
1. Abra o navegador e acesse a interface web do T.A.R.S. (`http://localhost:3000` ou URL corporativa).
2. Faça login com sua conta autorizada (@ellaexecutivesearch.com).
3. Clique em **"Nova Entrevista"** para iniciar a sessão.

---

### Passo 4: Iniciar a Captura com Um Clique ("Conectar companion")
No Cockpit Web, o card **"Canal do Candidato"** exibe o botão **"Conectar companion"**:
1. Basta clicar no botão **"Conectar companion"**.
2. O navegador abrirá automaticamente o aplicativo da barra de menus via link seguro (`tars-companion://join`), conectando o áudio da chamada instantaneamente.
3. O indicador de status do candidato fica verde no Cockpit Web:
   - 🟢 **Áudio do Sistema (ScreenCaptureKit Process Tap):** Ativo e capturando o áudio da reunião.

*O canal do entrevistador (microfone) é capturado diretamente pelo navegador com o headset selecionado no painel do Cockpit.*

---

## 4. Script de Consentimento & Conformidade (LGPD)

Antes de iniciar a gravação e a conversa técnica, faça a leitura do aviso obrigatório de ciência:

> *"Olá, [Nome do Candidato]. Para fins de registro da nossa avaliação de competências executivas e geração da síntese da entrevista, utilizaremos nosso assistente de transcrição em tempo real T.A.R.S. Todos os dados são confidenciais e tratados em conformidade com as diretrizes de privacidade e LGPD da nossa organização. Você concorda com o acompanhamento da entrevista?"*

Após a confirmação verbal positiva do candidato, clique na caixa **"Consentimento verbal registrado"** no Cockpit Web e inicie a condução da sessão.

---

## 5. Como Usar o Cockpit Durante a Entrevista

1. **Linha do Tempo em Tempo Real:** A fala do entrevistador aparece com o selo **[Entrevistador]** e a do candidato como **[Candidato]**.
2. **Sugestões Contextuais:** No painel lateral, o T.A.R.S. sugere perguntas de follow-up baseadas nas respostas do candidato e nos critérios da vaga.
3. **Anotações Ancoradas:** Digite notas rápidas; elas ficam automaticamente vinculadas ao timestamp exato da fala correspondente.
4. **Lacunas de Cobertura:** Caso ocorra uma desconexão temporária, a linha do tempo exibirá um aviso informativo não editável (ex: `[12:30 - 12:35] Lacuna de Áudio: Buffer de captura`), garantindo que ninguém tome decisões sobre dados ausentes.

---

## 6. Encerramento da Entrevista

1. Ao término da chamada, clique no botão **"Encerrar Entrevista"** no Cockpit Web.
2. O sistema fará a drenagem final do áudio, fechará o fluxo com segurança e gerará o **Relatório Executivo de Avaliação**.
3. O aplicativo de menu **TarsCompanion** encerra a captura automaticamente (ou você pode clicar em "Parar" no menu bar).

---

## 7. Solução de Problemas Rápidos

| Sintoma | Causa Mais Comum | Ação Recomendada |
| :--- | :--- | :--- |
| **Indicador "Áudio do Sistema" cinza ou com alerta** | Permissão de gravação de tela negada ou aplicativo não iniciado. | Acesse *Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela* e garanta que a permissão esteja habilitada. |
| **As duas vozes aparecem rotuladas como "Candidato"** | O som da chamada está saindo pelos alto-falantes e entrando pelo microfone. | Conecte um headset ou fones de ouvido e selecione o fone como saída de áudio. |
| **Aviso de "ADC Expirado" no servidor** | A credencial diária do Google Cloud expirou. | Execute `gcloud auth application-default login` no servidor. |
