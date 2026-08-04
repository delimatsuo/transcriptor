# Checklist de pré-voo — entrevista T.A.R.S.

Use esta lista antes de toda entrevista real. O pré-voo só passa quando os dois
canais de captura estiverem ativos.

1. No macOS, abra **Ajuste de Áudio e MIDI**. Em **Dispositivo de Saída**,
   selecione o **Dispositivo de Saída Múltipla** e confirme que ele contém
   **BlackHole 2ch** e os fones de ouvido reais. Não deixe a saída em fones
   externos: nessa configuração o BlackHole captura silêncio.
2. Coloque o headset. Não use alto-falantes: o vazamento entre os canais
   compromete a atribuição de rótulos.
3. Com um áudio normal tocando (por exemplo, YouTube) e falando ao microfone,
   execute:

   ```bash
   .venv/bin/python3 -m backend.scripts.preflight_audio
   ```

   Continue somente se `microphone` e `system-audio` mostrarem `PASS`.
4. No início da entrevista, apresente o aviso de transcrição, obtenha a
   confirmação verbal e registre a caixa de ciência antes de iniciar a sessão.
   Se a pessoa não concordar, siga sem gravação.
5. Inicie backend e frontend. Confirme que o frontend está em
   `http://localhost:3003` antes de criar a sessão.
6. Depois da entrevista, revise o transcript: os rótulos **Entrevistador** e
   **Candidato** devem aparecer; procure lacunas nas transições de
   aproximadamente 4 minutos e 30 segundos. Registre cada anomalia como um
   defeito, incluindo horário, canal afetado e trecho do log.
