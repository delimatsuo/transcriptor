# Checklist de pré-voo — entrevista T.A.R.S.

Use esta lista antes de toda entrevista real. O pré-voo só passa quando os dois
canais de captura estiverem ativos **e isolados entre si**.

0. **Reautentique o Google Cloud no dia da entrevista, antes de tudo:**

   ```bash
   gcloud auth application-default login
   ```

   A política da organização expira a credencial ADC **diariamente**
   (`invalid_grant: invalid_rapt`, confirmado em 2026-08-04 e 2026-08-05). O
   sintoma da credencial expirada NÃO é um erro: o backend simplesmente
   **trava em silêncio** em qualquer chamada ao Firestore/STT. Se qualquer
   endpoint demorar mais de ~10 s, suspeite de ADC expirado antes de qualquer
   outra hipótese. O backend agora valida e atualiza a ADC na inicialização,
   antes de ficar pronto; se a operação falhar ou exceder ~10 s, ele encerra
   com `ADC expirado — rode: gcloud auth application-default login`.

   **Acesso Week 4:** configure no `.env` o `AUTH_ALLOWED_EMAILS` com a conta
   Google autorizada (lista exata, sem curingas) e confira a configuração web
   Firebase em `.env.local` (`NEXT_PUBLIC_FIREBASE_*`). Entre com essa conta e
   confirme que o nome/e-mail aparecem no cabeçalho antes de criar qualquer
   sessão. A autenticação Firebase atribui o entrevistador e o `org_id`
   interno; ela não transforma este computador/Admin SDK em uma fronteira de
   segurança hospedada. Não use dados reais até o gate de hospedagem e a
   migração/quarentena de registros legados serem aprovados.

1. **Captura Nativa (Zero Configuração / Padrão Wispr):**
   - O aplicativo utiliza APIs nativas do macOS (`ScreenCaptureKit` para áudio do sistema/candidato e `AVAudioEngine` para o microfone do entrevistador).
   - **Não é necessário instalar nem configurar BlackHole, Cabos Virtuais ou Dispositivo de Saída Múltipla no Ajuste de Áudio e MIDI.**
   - O macOS solicitará apenas a permissão padrão de gravação de áudio do sistema/tela e microfone na primeira execução.
2. Coloque o headset para manter isolamento acústico natural entre o som dos fones e o microfone físico.
3. O microfone padrão e a saída de áudio normal do sistema são utilizados automaticamente sem nenhuma alteração manual de configurações de áudio no computador.
4. **Isolamento de Fontes:**
   - Com o microfone em silêncio, o áudio reproduzido na chamada (Zoom/Meet/Teams) é capturado diretamente pelo `ScreenCaptureKit` e rotulado como **Candidato**.
   - A voz do entrevistador no microfone é capturada pelo `AVAudioEngine` e rotulada como **Entrevistador**.
   - Zero interferência ou dependência de roteamento de hardware.

   **Gate físico reproduzível no macOS:** para evidência de release, não use
   instruções enviadas por chat para sincronizar a fala. Execute uma fase por
   processo com o harness abaixo, sempre a partir de um worktree limpo no SHA
   exato. O próprio processo anuncia quando falar, rejeita picos isolados,
   fecha e drena totalmente cada stream do STT e imprime somente métricas sem
   texto transcrito ou áudio bruto:

   ```bash
   # Saída normal do Mac = AirPods; fale após o aviso audível.
   .venv/bin/python3 -m backend.scripts.physical_audio_gate \
     --phase microphone \
     --expected-sha "$(git rev-parse HEAD)" \
     --send-to-provider \
     --confirm-provider-audio

   # Saída do Mac = Transcriptor Output; não fale nesta fase.
   .venv/bin/python3 -m backend.scripts.physical_audio_gate \
     --phase system-audio \
     --expected-sha "$(git rev-parse HEAD)" \
     --send-to-provider \
     --confirm-provider-audio
   ```

   A flag de confirmação significa que o áudio desta janela será enviado ao
   Google Cloud Speech-to-Text no projeto configurado; nada é salvo em disco e
   o conteúdo reconhecido não é impresso. A fase ativa exige sinal sustentado,
   callbacks sem erro, pelo menos 20 caracteres finais e drenagem completa; a
   fonte isolada exige zero caracteres finais. Rode cada fase duas vezes.
   Qualquer troca de fonte,
   índice, taxa de amostragem, código ou Git index invalida a evidência e exige
   novo vínculo ao SHA.

6. No início da entrevista, apresente o aviso de transcrição, obtenha a
   confirmação verbal e registre a caixa de ciência antes de iniciar a sessão.
   Se a pessoa não concordar, siga sem transcrição.
7. Inicie backend e frontend. Use `npm run dev -- -p 3003` durante o
   desenvolvimento ou `npm run start -- -p 3003` após o build. Ambos fixam o
   servidor Next em `127.0.0.1`; rejeite o pré-voo se ele escutar em `0.0.0.0`
   ou em qualquer interface de rede. Abra `http://localhost:3003` antes de criar
   a sessão (o processo continua escutando somente em `127.0.0.1`).
8. Ao encerrar, pare de falar e encerre a sessão. O backend para a captura,
   envia o áudio que ainda estiver na fila, fecha a entrada do STT e aguarda
   por até ~10 s os resultados finais e sua persistência. Confirme que o último
   enunciado e os dados da sessão persistiram antes de fechar a aplicação.

   - Se aparecer **Transcrição incompleta**, não use nem gere o relatório final;
     revise o fim da entrevista e preserve o log para investigação.
   - Se aparecer **Encerramento não confirmado: a captura pode continuar
     ativa**, mantenha a aplicação aberta e tente encerrar novamente. A tela
     permanece no estado ao vivo até receber uma confirmação terminal do
     backend; não presuma que a captura parou.
9. Depois da entrevista, revise o transcript: os rótulos **Entrevistador** e
   **Candidato** devem aparecer; procure lacunas nas transições de
   aproximadamente 4 minutos e 30 segundos. Registre cada anomalia como um
   defeito, incluindo horário, canal afetado e trecho do log.
