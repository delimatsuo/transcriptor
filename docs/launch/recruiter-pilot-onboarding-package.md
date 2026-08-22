# T.A.R.S. Recruiter Pilot Onboarding Package

**Version:** 1.0.0 (Launch Pilot Edition)  
**Target Audience:** Executive Search Recruiters & Interviewers (macOS & Windows 11)  
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
   - **Windows:** Windows 11 (Versão 22H2 ou superior).
2. **Headset ou Fones de Ouvido:** **Obrigatório.** O uso de fones evita que a voz do candidato ecoe no seu microfone físico, garantindo que os rótulos de quem está falando fiquem 100% corretos.
3. **Navegador:** Google Chrome, Microsoft Edge ou Safari atualizados.

---

## 3. Passo a Passo: Preparação Rápida (3 Minutos)

### Passo 1: Conceder Permissão de Gravação de Tela e Áudio do Sistema
Antes de baixar ou executar o companion, conceda a permissão do macOS:
1. Abra **Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela e Áudio do Sistema**.
2. Habilite a permissão para o **seu aplicativo de Terminal** (ex.: Terminal.app ou iTerm2) — é o Terminal quem recebe a permissão do sistema, não o binário `tars-companion` em si.
3. Se `tars-companion` ainda não aparecer na lista, execute-o uma vez (ele será recusado com uma mensagem de erro), volte a este painel e habilite a entrada que aparecer.

---

### Passo 2: Fazer o Download do Executável do Companion
Baixe o arquivo único correspondente ao seu sistema operacional:
- **macOS:** `dist/macos/tars-companion`
- **Windows:** `dist/windows-x64/tars-companion.exe` (ou `windows-arm64` para computadores Snapdragon/ARM)

> **Nota para macOS:** Na primeira execução, o macOS exibirá uma caixa solicitando permissão para "Gravação de Tela e Áudio do Sistema" e "Microfone". Basta clicar em **Permitir**.

---

### Passo 3: Acessar o Cockpit Web
1. Abra o navegador e acesse a interface web do T.A.R.S. (`http://localhost:3000`).
2. Faça login com sua conta Google autorizada.
3. Clique em **"Nova Entrevista"** para obter o seu `Session ID` (exemplo: `sess_exec_20260821`).

---

### Passo 4: Iniciar a Captura de Áudio
No Cockpit Web, o card **"Canal do Candidato"** já exibe o comando de inicialização pronto para copiar, com o seu `Session ID` e a chave de stream preenchidos. Clique em **Copiar** e cole no terminal. O comando tem este formato:

**No macOS:**
```bash
./tars-companion --session-id SEU_SESSION_ID --stream-key SUA_CHAVE --sources system_audio
```

**Windows: indisponível nesta fase.** O companion Windows é um esqueleto sem captura real; o piloto atual é macOS-somente.

Ao rodar o comando, o indicador de status do candidato fica verde no Cockpit Web:
- 🟢 **Áudio do Sistema (ScreenCaptureKit):** Ativo e capturando o áudio da chamada — canal do candidato, controlado pelo companion.

O indicador de **Microfone** (canal do entrevistador) é ativado separadamente, direto no navegador: quando solicitado, clique em **Permitir** o acesso ao microfone e selecione seu headset no seletor de dispositivo do Cockpit. Ele não depende do companion.

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
3. No terminal do companion, pressione `Ctrl + C` para finalizar o processo.

---

## 7. Solução de Problemas Rápidos

| Sintoma | Causa Mais Comum | Ação Recomendada |
| :--- | :--- | :--- |
| **Indicador "Áudio do Sistema" cinza ou com alerta** | Permissão de gravação de tela negada ou aplicativo não iniciado. | Acesse *Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela* e garanta que a permissão esteja habilitada. |
| **As duas vozes aparecem rotuladas como "Candidato"** | O som da chamada está saindo pelos alto-falantes e entrando pelo microfone. | Conecte um headset ou fones de ouvido e selecione o fone como saída de áudio. |
| **Aviso de "ADC Expirado" no servidor** | A credencial diária do Google Cloud expirou. | Execute `gcloud auth application-default login` no servidor. |
