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

1. No macOS, abra **Ajuste de Áudio e MIDI**. Em **Dispositivo de Saída**,
   selecione o **Dispositivo de Saída Múltipla** e confirme que ele contém
   **BlackHole 2ch** e os fones de ouvido reais. Não deixe a saída em fones
   externos: nessa configuração o BlackHole captura silêncio.
2. Coloque o headset. Não use alto-falantes: o vazamento entre os canais
   compromete a atribuição de rótulos.
3. Sempre que possível, defina `MICROPHONE_DEVICE_NAME` com o nome do microfone
   do headset. Não deixe essa configuração vazia por conveniência: se for
   necessário usar o padrão do sistema, confirme no resultado do pré-voo que o
   índice e o nome resolvidos correspondem ao microfone correto.
4. Com o headset colocado, reproduza áudio do sistema no volume normal e fale
   ao microfone durante a janela de medição indicada. Execute:

   ```bash
   .venv/bin/python3 -m backend.scripts.preflight_audio
   ```

   Continue somente se `microphone` e `system-audio` mostrarem `PASS`. Registre
   o SHA (`git rev-parse HEAD`) e os índices e nomes exatos dos dois dispositivos
   impressos pelo comando.
5. Faça o **teste obrigatório de isolamento das fontes** em uma sessão de teste:

   - Com o microfone em silêncio, reproduza fala somente pelo sistema. O texto
     deve aparecer apenas como **Candidato**.
   - Pause a reprodução do sistema e fale somente ao microfone. O texto deve
     aparecer apenas como **Entrevistador**.
   - Alterne as duas fontes, uma de cada vez. Rejeite o pré-voo se enunciados
     substanciais iguais aparecerem nos dois rótulos.

   O ensaio de 4 de agosto, feito com áudio do YouTube nos alto-falantes do
   ambiente, apresentou conteúdo duplicado em **Candidato** e
   **Entrevistador**; ele não qualifica a atribuição de falantes.

   **Evidência de que o procedimento funciona (2026-08-05):** o reteste com
   headset (vídeo audível apenas nos fones, saída = Dispositivo de Saída
   Múltipla) produziu isolamento perfeito — conteúdo do vídeo somente como
   **Candidato**, fala do entrevistador somente como **Entrevistador**, zero
   vazamento nos dois sentidos (sessão `9060cd3c`). A atribuição por fonte
   dupla está verificada de ponta a ponta nesta configuração. Armadilha
   confirmada no mesmo teste: ao trocar de dispositivo de áudio (ex.: colocar
   fones), o macOS pode mover a saída do sistema para fora do Dispositivo de
   Saída Múltipla — o passo 1 deve ser reconferido SEMPRE que o hardware de
   áudio mudar (log do backend acusa `audio_device_silent label=Candidato`).
6. No início da entrevista, apresente o aviso de transcrição, obtenha a
   confirmação verbal e registre a caixa de ciência antes de iniciar a sessão.
   Se a pessoa não concordar, siga sem transcrição.
7. Inicie backend e frontend. Confirme que o frontend está em
   `http://localhost:3003` antes de criar a sessão.
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
