#!/usr/bin/env python3
"""Prova ao vivo do canal do candidato (piloto-solo).

Roda o sistema real de ponta a ponta na máquina do proprietário:

    say (pt-BR)  ->  alto-falantes  ->  ScreenCaptureKit (companion nativo)
                 ->  gateway WebSocket (backend real)  ->  Google STT
                 ->  segmento final rotulado "Candidato"

Nada aqui é simulado: backend real (uvicorn), binário `tars-companion` real,
Google STT real, `say` real. O único trecho injetado por software é o canal do
*entrevistador* (fase 6), que envia PCM de fala real gerada por `say` pelo mesmo
gateway com `source="microphone"` — é assim que a rotulagem por fonte é provada
sem precisar de um humano falando ao microfone.

Códigos de saída:
    0  todas as fases executadas passaram
    1  alguma asserção falhou (defeito real)
    2  preflight de ambiente falhou (ADC, porta, voz, binário)
   42  BLOQUEADO por permissão TCC ausente (Gravação de Tela e Áudio do Sistema)

Uso:
    .venv/bin/python scripts/verify_live_system_audio.py [--with-restart-drill]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pty
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

import requests
import websockets
from websockets.asyncio.client import connect as ws_connect

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPANION_DIR = REPO_ROOT / "companion" / "native-macos"
COMPANION_BIN = COMPANION_DIR / ".build" / "release" / "tars-companion"
EVIDENCE_DOC = REPO_ROOT / "docs" / "launch" / "2026-08-21-solo-live-system-audio-evidence.md"

PORT = 8010
BASE_URL = f"http://127.0.0.1:{PORT}"
WS_BASE = f"ws://127.0.0.1:{PORT}/api/stream/native"

CANDIDATE_SENTENCE = (
    "O candidato tem dez anos de experiência em liderança de vendas e fala inglês fluente"
)
INTERVIEWER_SENTENCE = "Aqui fala o entrevistador fazendo uma pergunta"
RESTART_SENTENCE = "Esta frase vem depois do reinício da captura do candidato"

CANDIDATE_WORDS = {"candidato", "experiencia", "vendas", "ingles"}
CANDIDATE_MIN_HITS = 2
INTERVIEWER_WORDS = {"entrevistador", "pergunta"}
INTERVIEWER_MIN_HITS = 1
RESTART_WORDS = {"reinicio", "captura"}
RESTART_MIN_HITS = 1

SAMPLE_RATE = 16_000
FRAME_MS = 50
FRAME_BYTES = SAMPLE_RATE * 2 * FRAME_MS // 1000  # 1600 bytes = 50 ms mono s16le

# Exit codes
EXIT_OK, EXIT_FAILED, EXIT_PREFLIGHT, EXIT_TCC_BLOCKED = 0, 1, 2, 42

SCRATCH = Path(os.environ.get("TMPDIR", "/tmp")) / "tars_live_proof"


# --------------------------------------------------------------------------
# Relatório de fases
# --------------------------------------------------------------------------

class Phases:
    """Registro ordenado de fases com resultado individual (PASS/FAIL/BLOQUEADO)."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.facts: dict[str, object] = {}

    def record(self, name: str, status: str, detail: str = "") -> None:
        self.rows.append({"name": name, "status": status, "detail": detail})
        icon = {"PASS": "✓", "FAIL": "✗", "BLOQUEADO": "⏸", "PULADO": "–"}.get(status, "?")
        print(f"  {icon} {name}: {status}" + (f" — {detail}" if detail else ""), flush=True)

    @property
    def failed(self) -> bool:
        return any(r["status"] == "FAIL" for r in self.rows)

    @property
    def blocked(self) -> bool:
        return any(r["status"] == "BLOQUEADO" for r in self.rows)


def banner(title: str) -> None:
    print(f"\n▶ {title}", flush=True)


def normalize(text: str) -> str:
    """Minúsculas sem acentos — o STT varia na acentuação entre execuções."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def hits(text: str, words: set[str]) -> set[str]:
    normalized = normalize(text)
    return {w for w in words if w in normalized}


# --------------------------------------------------------------------------
# Fase 1 — Preflight
# --------------------------------------------------------------------------

# `say -v '?'` imprime "<nome>  <locale>  # <exemplo>"; o nome pode conter
# espaços e parênteses ("Eddy (Portuguese (Brazil))"), então o corte tem de ser
# feito no token de locale — não no primeiro par de espaços, que deixaria o
# "pt_BR" grudado no nome registrado na evidência.
VOICE_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<locale>[a-z]{2}_[A-Z]{2})\s+#")


def pick_voice() -> str | None:
    """Voz pt-BR preferida (Eddy, Flo); qualquer pt_BR serve como alternativa."""
    listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True)
    br_voices: list[str] = []
    for line in listing.stdout.splitlines():
        match = VOICE_LINE.match(line)
        if match and match.group("locale") == "pt_BR":
            br_voices.append(match.group("name").strip())
    for preferred in ("Eddy", "Flo"):
        for voice in br_voices:
            if voice.startswith(preferred):
                return voice
    return br_voices[0] if br_voices else None


def phase_preflight(ph: Phases) -> bool:
    banner("Fase 1/10 — Preflight de ambiente")

    # ADC: apenas o código de saída. O token nunca é lido, impresso ou guardado.
    adc = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if adc.returncode != 0:
        ph.record("Preflight ADC", "FAIL", "ADC expirado: rode 'gcloud auth application-default login'")
        return False
    ph.record("Preflight ADC", "PASS", "credenciais padrão válidas (verificado por exit code)")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            ph.record("Preflight porta", "FAIL", f"porta {PORT} já está em uso")
            return False
    ph.record("Preflight porta", "PASS", f"porta {PORT} livre")

    voice = pick_voice()
    if not voice:
        ph.record("Preflight voz pt-BR", "FAIL", "nenhuma voz pt_BR instalada")
        return False
    ph.facts["voice"] = voice
    ph.record("Preflight voz pt-BR", "PASS", f"voz '{voice}'")

    if not COMPANION_BIN.exists() or _binary_is_stale():
        print("  … compilando o companion (swift build -c release)", flush=True)
        build = subprocess.run(
            ["swift", "build", "-c", "release"],
            cwd=str(COMPANION_DIR),
            capture_output=True,
            text=True,
        )
        if build.returncode != 0 or not COMPANION_BIN.exists():
            ph.record("Preflight binário companion", "FAIL", build.stderr.strip()[-400:])
            return False
        ph.record("Preflight binário companion", "PASS", "compilado agora")
    else:
        ph.record("Preflight binário companion", "PASS", "binário existente já atualizado")
    return True


def _binary_is_stale() -> bool:
    sources = list((COMPANION_DIR / "Sources").rglob("*.swift"))
    if not sources:
        return False
    return COMPANION_BIN.stat().st_mtime < max(s.stat().st_mtime for s in sources)


# --------------------------------------------------------------------------
# Fase 2 — Backend real
# --------------------------------------------------------------------------

def phase_backend(ph: Phases) -> tuple[subprocess.Popen, IO[bytes]] | None:
    """Devolve (processo, handle do log) — o handle é fechado pelo `finally`."""
    banner("Fase 2/10 — Subindo o backend real (uvicorn)")
    env = dict(os.environ)
    env["AUTH_BYPASS"] = "true"
    env.pop("HOST_AUDIO_CAPTURE_ENABLED", None)  # captura legada no host fica desligada

    log = open(SCRATCH / "backend.log", "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(PORT)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            ph.record("Backend up", "FAIL", f"uvicorn saiu com código {proc.returncode}")
            log.close()
            return None
        try:
            if requests.get(f"{BASE_URL}/healthz", timeout=2).status_code == 200:
                ph.record("Backend up", "PASS", f"/healthz respondendo em :{PORT}")
                return proc, log
        except requests.RequestException:
            time.sleep(0.5)
    ph.record("Backend up", "FAIL", "timeout esperando /healthz")
    proc.terminate()
    log.close()
    return None


# --------------------------------------------------------------------------
# Fase 3 — Sessão + chave de stream
# --------------------------------------------------------------------------

def phase_session(ph: Phases) -> tuple[str, str] | None:
    banner("Fase 3/10 — Criando sessão de entrevista")
    resp = requests.post(
        f"{BASE_URL}/api/sessions",
        params={"mode": "interview", "title": "live-proof"},
        timeout=30,
    )
    if resp.status_code != 200:
        ph.record("Sessão criada", "FAIL", f"HTTP {resp.status_code}")
        return None
    body = resp.json()
    session_id, stream_key = body.get("session_id"), body.get("stream_key")
    if not session_id or not stream_key:
        ph.record("Sessão criada", "FAIL", "resposta sem session_id ou stream_key")
        return None
    # A chave é um segredo: registra-se apenas a presença e o comprimento.
    ph.record("Sessão criada", "PASS", f"session_id={session_id}, stream_key presente ({len(stream_key)} chars)")
    return session_id, stream_key


# --------------------------------------------------------------------------
# Fase 4 — Sonda de chave errada (não depende de TCC; roda cedo de propósito)
# --------------------------------------------------------------------------

# Só estes dois desfechos provam que o gateway REJEITOU a chave. Um OSError, um
# timeout ou qualquer outro status provam apenas que a conexão não vingou — o que
# aconteceria igualmente com o backend fora do ar — e por isso contam como FAIL.
REJECT_HTTP_STATUSES = {401, 403}
REJECT_CLOSE_CODE = 1008


def stream_subprotocols(stream_key: str) -> list[str]:
    """Retorna os subprotocolos WebSocket na ordem canônica do gateway."""
    return ["tars-stream", stream_key]


async def _probe_invalid_key(session_id: str) -> tuple[bool, str]:
    """Rejeição positiva: 401/403 no handshake, ou fechamento 1008 sem aceitar frames."""
    url = f"{WS_BASE}/{session_id}"
    try:
        async with ws_connect(url, subprotocols=["tars-stream", "WRONG"], open_timeout=10) as ws:
            # Handshake aceito: o gateway ainda tem de fechar sem aceitar frames.
            await ws.send(encode_frame(session_id, "microphone", 0, 0, b"\x00" * FRAME_BYTES))
            try:
                await asyncio.wait_for(ws.recv(), timeout=5)
            except websockets.exceptions.ConnectionClosed as closed:
                code = closed.rcvd.code if closed.rcvd else None
                if code == REJECT_CLOSE_CODE:
                    return True, f"fechada com código {code} sem aceitar frames"
                return False, f"fechada com código {code}, esperado {REJECT_CLOSE_CODE}"
            except asyncio.TimeoutError:
                return False, "gateway manteve a conexão aberta com stream_key inválida"
            return False, "gateway respondeu dados com stream_key inválida"
    except websockets.exceptions.InvalidStatus as exc:
        status = exc.response.status_code
        if status in REJECT_HTTP_STATUSES:
            return True, f"handshake rejeitado com HTTP {status}"
        return False, f"HTTP {status} não é uma rejeição de autenticação (esperado {sorted(REJECT_HTTP_STATUSES)})"
    except websockets.exceptions.ConnectionClosed as closed:
        code = closed.rcvd.code if closed.rcvd else None
        if code == REJECT_CLOSE_CODE:
            return True, f"fechada com código {code}"
        return False, f"fechada com código {code}, esperado {REJECT_CLOSE_CODE}"
    except Exception as exc:
        # Ausência de sucesso não é prova de rejeição.
        return False, f"nenhuma rejeição observada — a conexão falhou por {type(exc).__name__}: {exc}"


async def _probe_valid_key(session_id: str, stream_key: str) -> tuple[bool, str]:
    """Controle positivo: sem uma aceitação no mesmo instante, a rejeição acima
    não distingue "autenticação funciona" de "o gateway recusa todo mundo".
    Nenhum frame é enviado aqui, então nenhum StreamManager é criado."""
    url = f"{WS_BASE}/{session_id}"
    try:
        async with ws_connect(url, subprotocols=stream_subprotocols(stream_key), open_timeout=10) as ws:
            await ws.send(json.dumps({"type": "hello", "sources": ["microphone"]}))
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=2)
                return False, f"gateway respondeu/fechou inesperadamente com a chave válida: {message!r}"
            except asyncio.TimeoutError:
                pass  # silêncio = conexão aceita e mantida aberta
            await ws.close()
            return True, "conexão aceita e mantida aberta, encerrada limpa"
    except Exception as exc:
        return False, f"chave válida recusada: {type(exc).__name__}: {exc}"


def phase_wrong_key(ph: Phases, session_id: str, stream_key: str) -> None:
    banner("Fase 4/10 — Sondas de chave de stream (rejeição + controle positivo)")

    async def both() -> tuple[tuple[bool, str], tuple[bool, str]]:
        return await _probe_invalid_key(session_id), await _probe_valid_key(session_id, stream_key)

    (bad_ok, bad_detail), (good_ok, good_detail) = asyncio.run(both())
    ph.record("Chave inválida rejeitada", "PASS" if bad_ok else "FAIL", bad_detail)
    ph.record("Chave válida aceita (controle positivo)", "PASS" if good_ok else "FAIL", good_detail)


# --------------------------------------------------------------------------
# Fase 5 — Companion nativo (ScreenCaptureKit)
# --------------------------------------------------------------------------

class CompanionRun:
    """Um processo `tars-companion` ligado a um pty.

    O pty é obrigatório: com stdout redirecionado para arquivo ou pipe, o
    runtime do Swift usa buffer de bloco e a linha de prontidão
    ("System audio capture active") só apareceria quando o processo terminasse
    — tarde demais para servir de sinal. Com um pty o stdout vira line-buffered
    e a linha chega assim que é impressa.
    """

    def __init__(self, session_id: str, stream_key: str, tag: str) -> None:
        self.out_path = SCRATCH / f"companion-{tag}.log"
        self._master, slave = pty.openpty()
        self.proc = subprocess.Popen(
            [
                str(COMPANION_BIN),
                "--session-id", session_id,
                "--stream-key", stream_key,
                "--sources", "system_audio",
                "--gateway", WS_BASE,
            ],
            cwd=str(COMPANION_DIR),
            stdout=slave,
            stderr=slave,
        )
        os.close(slave)
        self._chunks: list[str] = []
        self._lock = threading.Lock()
        # Dreno contínuo: um pty cheio bloquearia o processo filho.
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self) -> None:
        while True:
            try:
                data = os.read(self._master, 4096)
            except OSError:
                break
            if not data:
                break
            with self._lock:
                self._chunks.append(data.decode("utf-8", errors="replace"))

    def output(self) -> str:
        with self._lock:
            return "".join(self._chunks)

    def wait_for_capture(self, timeout: float = 25.0) -> tuple[str, str]:
        """Devolve ('ativo'|'tcc'|'falhou'|'timeout', saída do processo)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.output()
            if "System audio capture active" in text:
                return "ativo", text
            code = self.proc.poll()
            if code is not None:
                if code == 2:
                    return "tcc", text
                return "falhou", text
            time.sleep(0.4)
        return "timeout", self.output()

    def kill(self) -> None:
        """SIGKILL — sem chance de encerramento gracioso, como um crash real."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        self._teardown()

    def _teardown(self) -> None:
        try:
            os.close(self._master)
        except OSError:
            pass
        self._reader.join(timeout=2)
        self.out_path.write_text(self.output(), encoding="utf-8")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._teardown()


def phase_companion(ph: Phases, session_id: str, stream_key: str) -> CompanionRun | None:
    banner("Fase 5/10 — Companion nativo capturando áudio do sistema")
    run = CompanionRun(session_id, stream_key, "primary")
    state, output = run.wait_for_capture()

    if state == "ativo":
        ph.record("Companion — captura de sistema ativa", "PASS", "ScreenCaptureKit iniciado")
        return run

    tail = "\n".join(output.strip().splitlines()[-6:])
    if state == "tcc":
        ph.facts["tcc_message"] = tail
        ph.record(
            "Companion — captura de sistema ativa",
            "BLOQUEADO",
            "permissão TCC ausente (companion saiu com código 2)",
        )
        print("\n" + tail + "\n", flush=True)
        run.stop()
        return None

    ph.record("Companion — captura de sistema ativa", "FAIL", f"estado={state}; {tail[-300:]}")
    run.stop()
    return None


# --------------------------------------------------------------------------
# Fase 6 — Áudio do candidato (alto-falante -> ScreenCaptureKit)
# --------------------------------------------------------------------------

def speak(voice: str, sentence: str) -> int:
    """Devolve o código de saída do `say` — um PASS incondicional aqui
    registraria "áudio reproduzido" mesmo quando nada tocou."""
    return subprocess.run(["say", "-v", voice, sentence], check=False).returncode


def phase_candidate_audio(ph: Phases, voice: str) -> None:
    banner("Fase 6/10 — Falando a frase do candidato pelos alto-falantes")
    codes: list[int] = []
    for i in range(2):
        codes.append(speak(voice, CANDIDATE_SENTENCE))
        if i == 0:
            time.sleep(2)
    if any(code != 0 for code in codes):
        ph.record("Áudio do candidato reproduzido", "FAIL", f"`say` retornou {codes}")
        return
    ph.record("Áudio do candidato reproduzido", "PASS", "frase dita 2x pela saída do sistema")


# --------------------------------------------------------------------------
# Fase 7 — Canal do entrevistador (injeção de PCM real de fala)
# --------------------------------------------------------------------------

def encode_frame(session_id: str, source: str, sequence: int, first_sample: int, pcm: bytes) -> bytes:
    """4 bytes big-endian (tamanho do cabeçalho) + cabeçalho JSON + PCM cru."""
    header = json.dumps(
        {
            "session_id": session_id,
            "source": source,
            "sequence": sequence,
            "first_sample": first_sample,
            "captured_at_ms": int(time.time() * 1000),
            "sample_rate": SAMPLE_RATE,
            "channel_count": 1,
            "duration_ms": len(pcm) * 1000 // (SAMPLE_RATE * 2),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return len(header).to_bytes(4, "big") + header + pcm


def synth_pcm(voice: str, sentence: str, tag: str) -> bytes:
    """Gera fala real em PCM 16 kHz mono s16le via `say`."""
    wav_path = SCRATCH / f"{tag}.wav"
    subprocess.run(
        ["say", "-v", voice, "-o", str(wav_path), "--data-format=LEI16@16000", sentence],
        check=True,
    )
    with wave.open(str(wav_path), "rb") as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == SAMPLE_RATE and wav.getsampwidth() == 2
        return wav.readframes(wav.getnframes())


class MicChannel:
    """Canal `source="microphone"` mantido aberto numa thread própria.

    Envia a fala real do entrevistador e **continua enviando silêncio** a 50 ms
    até `stop()`. Essa continuidade não é cosmética: o Google STT encerra um
    stream que fica sem requisições do cliente (`409 Stream timed out after
    receiving no more client requests`), o que marcaria o dreno como incompleto.
    O companion nativo alimenta o canal do sistema continuamente do mesmo jeito
    — aqui o harness apenas se comporta como o cliente real.
    """

    def __init__(self, session_id: str, stream_key: str, pcm: bytes) -> None:
        self._session_id = session_id
        self._url = f"{WS_BASE}/{session_id}"
        self._subprotocols = stream_subprotocols(stream_key)
        self._pcm = pcm
        self._stop = threading.Event()
        self.frames_sent = 0
        self.speech_frames = 0
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()

    def start(self, timeout: float = 15.0) -> bool:
        self._thread.start()
        return self._ready.wait(timeout)

    def _run(self) -> None:
        try:
            asyncio.run(self._pump())
        except BaseException as exc:  # registrado e reportado pela fase
            self.error = exc
            self._ready.set()

    async def _pump(self) -> None:
        silence = b"\x00" * FRAME_BYTES
        async with ws_connect(self._url, subprotocols=self._subprotocols, open_timeout=10) as ws:
            await ws.send(json.dumps({"type": "hello", "sources": ["microphone"]}))
            self._ready.set()
            sample = 0
            # 1) fala real do entrevistador
            for offset in range(0, len(self._pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                await ws.send(
                    encode_frame(self._session_id, "microphone", self.frames_sent, sample, self._pcm[offset:offset + FRAME_BYTES])
                )
                self.frames_sent += 1
                self.speech_frames += 1
                sample += FRAME_BYTES // 2
                await asyncio.sleep(FRAME_MS / 1000)  # ritmo de tempo real
            # 2) silêncio contínuo até a fase de parada
            while not self._stop.is_set():
                await ws.send(encode_frame(self._session_id, "microphone", self.frames_sent, sample, silence))
                self.frames_sent += 1
                sample += FRAME_BYTES // 2
                await asyncio.sleep(FRAME_MS / 1000)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)


def phase_interviewer_audio(ph: Phases, session_id: str, stream_key: str, voice: str) -> MicChannel | None:
    banner("Fase 7/10 — Injetando o canal do entrevistador (source=microphone)")
    pcm = synth_pcm(voice, INTERVIEWER_SENTENCE, "mic")
    channel = MicChannel(session_id, stream_key, pcm)
    if not channel.start() or channel.error is not None:
        ph.record("Canal do entrevistador enviado", "FAIL", f"não abriu o WebSocket: {channel.error}")
        return None
    speech_ms = len(pcm) // (SAMPLE_RATE * 2 // 1000)
    ph.record(
        "Canal do entrevistador enviado",
        "PASS",
        f"WebSocket aberto com a chave válida; {speech_ms} ms de fala real em quadros de 50 ms",
    )
    return channel


# --------------------------------------------------------------------------
# Fase 8 — Ensaio de reinício (só alcançável com TCC concedido)
# --------------------------------------------------------------------------

def phase_restart_drill(ph: Phases, run: CompanionRun, session_id: str, stream_key: str, voice: str) -> None:
    banner("Fase 8/10 — Ensaio de reinício do companion (SIGKILL + relançamento)")
    run.kill()
    time.sleep(1)
    again = CompanionRun(session_id, stream_key, "restart")
    # Registrado ANTES de qualquer verificação: se esta fase falhar ou levantar,
    # o `finally` do main precisa conseguir matar este processo de qualquer jeito.
    ph.facts["restart_run"] = again
    state, _ = again.wait_for_capture()
    if state != "ativo":
        ph.record("Reinício do companion", "FAIL", f"não recapturou após SIGKILL (estado={state})")
        return
    if speak(voice, RESTART_SENTENCE) != 0:
        ph.record("Reinício do companion", "FAIL", "`say` falhou ao reproduzir a frase pós-reinício")
        return
    time.sleep(2)
    ph.record("Reinício do companion", "PASS", "capturou de novo com a mesma stream_key")


# --------------------------------------------------------------------------
# Fase 9 — Parar e conferir o transcript
# --------------------------------------------------------------------------

def fetch_segments(session_id: str) -> list[dict]:
    resp = requests.get(f"{BASE_URL}/api/sessions/{session_id}/transcript", timeout=30)
    if resp.status_code != 200:
        return []
    return resp.json().get("segments", [])


def speaker_of(segment: dict) -> str:
    return segment.get("speaker_override") or segment.get("speaker") or ""


def phase_stop_and_assert(
    ph: Phases,
    session_id: str,
    expect_candidate: bool,
    expect_restart: bool,
    mic: "MicChannel | None",
    companion: "CompanionRun | None",
) -> None:
    banner("Fase 9/10 — Encerrando a sessão e conferindo o transcript")
    # Assentamento com os dois canais ainda transmitindo: é assim que uma
    # entrevista real chega ao /stop, e é o que permite ao STT fechar os
    # resultados finais em vez de abortar por inatividade.
    print("  … assentando 10 s com os canais ainda transmitindo", flush=True)
    time.sleep(10)
    pre_stop = fetch_segments(session_id)
    ph.facts["segments_pre_stop"] = len(pre_stop)

    # Só agora as fontes param — imediatamente antes do /stop.
    if mic is not None:
        mic.stop()
        ph.facts["mic_frames"] = mic.frames_sent
        ph.facts["mic_speech_frames"] = mic.speech_frames
        ph.facts["mic_bytes"] = mic.frames_sent * FRAME_BYTES
        # O canal só é "sustentado até o /stop" se o socket sobreviveu até aqui.
        # Uma morte no meio do caminho encerra a sustentação em silêncio, e a
        # evidência não pode afirmar continuidade que não houve.
        if mic.error is not None:
            ph.record(
                "Canal do entrevistador sustentado até o /stop",
                "FAIL",
                f"socket do microfone morreu durante a execução: "
                f"{type(mic.error).__name__}: {mic.error}",
            )
        else:
            ph.record(
                "Canal do entrevistador sustentado até o /stop",
                "PASS",
                f"{mic.frames_sent} frames entregues sem erro de socket",
            )
    if companion is not None:
        companion.stop()

    stop = requests.post(f"{BASE_URL}/api/sessions/{session_id}/stop", timeout=120)
    if stop.status_code != 200:
        ph.record("Sessão encerrada", "FAIL", f"HTTP {stop.status_code}")
        return
    ph.facts["transcription_complete"] = stop.json().get("transcription_complete")
    ph.record("Sessão encerrada", "PASS", f"transcription_complete={ph.facts['transcription_complete']}")

    segments = fetch_segments(session_id)
    finals = [s for s in segments if s.get("is_final")]
    ph.facts["segments_total"] = len(segments)
    ph.facts["segments_final"] = len(finals)
    ph.facts["transcript"] = [
        {"speaker": speaker_of(s), "text": s.get("text", "")} for s in finals
    ]
    print(f"  … {len(finals)} segmentos finais de {len(segments)} no total", flush=True)
    for seg in finals:
        print(f"      [{speaker_of(seg)}] {seg.get('text', '')}", flush=True)

    # --- Candidato (áudio do sistema via ScreenCaptureKit) ---
    if expect_candidate:
        matched = [
            (s, hits(s.get("text", ""), CANDIDATE_WORDS))
            for s in finals
            if speaker_of(s) == "Candidato"
        ]
        best = max((h for _, h in matched), key=len, default=set())
        if any(len(h) >= CANDIDATE_MIN_HITS for _, h in matched):
            ph.record(
                "Segmento final rotulado 'Candidato'",
                "PASS",
                f"palavras reconhecidas: {sorted(best)}",
            )
        else:
            ph.record(
                "Segmento final rotulado 'Candidato'",
                "FAIL",
                f"nenhum final 'Candidato' com ≥{CANDIDATE_MIN_HITS} de {sorted(CANDIDATE_WORDS)}"
                f" (melhor: {sorted(best)}, {len(matched)} segmentos do Candidato)",
            )
    else:
        ph.record(
            "Segmento final rotulado 'Candidato'",
            "BLOQUEADO",
            "canal do candidato nunca capturou (permissão TCC ausente)",
        )

    # --- Entrevistador (injeção pelo mesmo gateway com source=microphone) ---
    matched_i = [
        (s, hits(s.get("text", ""), INTERVIEWER_WORDS))
        for s in finals
        if speaker_of(s) == "Entrevistador"
    ]
    best_i = max((h for _, h in matched_i), key=len, default=set())
    if any(len(h) >= INTERVIEWER_MIN_HITS for _, h in matched_i):
        ph.record(
            "Segmento final rotulado 'Entrevistador'",
            "PASS",
            f"palavras reconhecidas: {sorted(best_i)}",
        )
    else:
        ph.record(
            "Segmento final rotulado 'Entrevistador'",
            "FAIL",
            f"nenhum final 'Entrevistador' com ≥{INTERVIEWER_MIN_HITS} de {sorted(INTERVIEWER_WORDS)}"
            f" (melhor: {sorted(best_i)}, {len(matched_i)} segmentos do Entrevistador)",
        )

    # --- Nenhum texto idêntico atribuído aos dois lados ---
    by_speaker: dict[str, set[str]] = {}
    for seg in finals:
        by_speaker.setdefault(speaker_of(seg), set()).add(normalize(seg.get("text", "")).strip())
    overlap = by_speaker.get("Candidato", set()) & by_speaker.get("Entrevistador", set())
    overlap.discard("")
    if overlap:
        ph.record("Sem duplicação entre falantes", "FAIL", f"texto idêntico nos dois falantes: {sorted(overlap)}")
    else:
        ph.record("Sem duplicação entre falantes", "PASS", "nenhum texto final compartilhado")

    # --- Ensaio de reinício ---
    if expect_restart:
        # A frase pós-reinício entrou pelo canal que foi morto (áudio do
        # sistema), então ela tem de reaparecer rotulada como "Candidato" —
        # exigir só a presença do texto não provaria que a captura voltou.
        post = [
            s
            for s in finals
            if speaker_of(s) == "Candidato"
            and len(hits(s.get("text", ""), RESTART_WORDS)) >= RESTART_MIN_HITS
        ]
        if post:
            ph.record(
                "Fala pós-reinício transcrita",
                "PASS",
                f"{len(post)} segmento(s) 'Candidato' após o SIGKILL",
            )
        else:
            ph.record(
                "Fala pós-reinício transcrita",
                "FAIL",
                "frase pós-reinício não reapareceu como 'Candidato' no transcript",
            )


# --------------------------------------------------------------------------
# Fase 10 — Documento de evidência
# --------------------------------------------------------------------------

def phase_evidence(ph: Phases, args: argparse.Namespace) -> None:
    banner("Fase 10/10 — Escrevendo o documento de evidência")
    sw = subprocess.run(["sw_vers"], capture_output=True, text=True).stdout.strip()
    machine = " / ".join(line.split(":", 1)[1].strip() for line in sw.splitlines() if ":" in line)
    arch = subprocess.run(["uname", "-m"], capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), capture_output=True, text=True
    ).stdout.strip()
    # Um commit sozinho não identifica o que rodou: com o working tree sujo, o
    # código exercitado não é o do commit. E o binário do companion é compilado
    # à parte, então a sua data é a única pista de qual revisão ele carrega.
    porcelain = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(REPO_ROOT), capture_output=True, text=True
    ).stdout.splitlines()
    # Qualquer saída conta como sujo, mas as duas causas têm pesos diferentes e
    # colapsá-las gastaria o sinal: arquivo versionado modificado significa que o
    # código exercitado não é o do commit; arquivo apenas não versionado, não.
    tracked_changes = [ln for ln in porcelain if not ln.startswith("??")]
    untracked = [ln for ln in porcelain if ln.startswith("??")]
    if tracked_changes:
        tree_state = f"**SUJO — {len(tracked_changes)} arquivo(s) versionado(s) modificado(s)**"
    elif untracked:
        tree_state = f"**SUJO — apenas {len(untracked)} arquivo(s) não versionado(s) presente(s)**"
    else:
        tree_state = "limpo"
    binary_mtime = (
        datetime.fromtimestamp(COMPANION_BIN.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        if COMPANION_BIN.exists()
        else "ausente"
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines = [
        "# Evidência — prova ao vivo do canal do candidato (piloto-solo)",
        "",
    ]

    if ph.failed:
        lines += [
            "> ## ❌ EXECUÇÃO COM FALHAS",
            ">",
            "> **Este documento NÃO comprova o canal do candidato.** Uma ou mais fases "
            "reprovaram (veja a tabela abaixo). O registro serve apenas para diagnóstico "
            "da tentativa malsucedida.",
            "",
        ]

    lines += [
        f"- **Gerado por:** `scripts/verify_live_system_audio.py`{' --with-restart-drill' if args.with_restart_drill else ''}",
        f"- **Data (UTC):** {now}",
        f"- **Máquina:** {machine} ({arch})",
        f"- **Commit:** `{commit}` — working tree: {tree_state}",
        f"- **Binário `tars-companion` exercitado:** compilado em {binary_mtime} (UTC)",
        f"- **Voz pt-BR usada:** {ph.facts.get('voice', 'n/d')}",
        f"- **Backend:** uvicorn real em `127.0.0.1:{PORT}`, `AUTH_BYPASS=true`, "
        "`HOST_AUDIO_CAPTURE_ENABLED` não definido",
        "- **STT:** Google Speech-to-Text real (ADC verificada apenas por código de saída; "
        "nenhum token foi lido, impresso ou gravado)",
        "- **Dependências Python:** `requests` e `websockets` já presentes no `.venv` — nada foi instalado",
    ]

    if tracked_changes:
        lines += [
            "",
            "> ⚠ **Arquivos versionados estavam modificados durante esta execução.** O código e "
            "o binário exercitados NÃO correspondem ao commit acima; reproduza a partir de uma "
            "árvore limpa antes de tratar este documento como evidência desse commit.",
        ]
    elif untracked:
        lines += [
            "",
            f"> ℹ Havia {len(untracked)} arquivo(s) não versionado(s) na árvore, mas nenhum "
            "arquivo versionado modificado — o código exercitado corresponde ao commit acima.",
        ]

    lines += [
        "",
        "## Resultado por fase",
        "",
        "| # | Fase | Resultado | Detalhe |",
        "|---|------|-----------|---------|",
    ]
    for i, row in enumerate(ph.rows, start=1):
        detail = row["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {i} | {row['name']} | **{row['status']}** | {detail} |")

    lines += [
        "",
        "## Contagens observadas",
        "",
        f"- Frames injetados no canal do entrevistador (`source=microphone`): "
        f"**{ph.facts.get('mic_frames', 0)}** quadros de 50 ms / 1600 B "
        f"({ph.facts.get('mic_bytes', 0) / 1000:.1f} kB), dos quais "
        f"**{ph.facts.get('mic_speech_frames', 0)}** de fala real e o restante de silêncio "
        "de sustentação até o `/stop`",
        "- Frames do canal do candidato (`source=system_audio`): produzidos pelo binário "
        "`tars-companion` via ScreenCaptureKit; o gateway não expõe um contador por fonte, "
        "então a prova desse canal é o segmento transcrito abaixo, não uma contagem",
        f"- Segmentos no transcript antes do `/stop`: **{ph.facts.get('segments_pre_stop', 0)}**",
        f"- Segmentos no transcript depois do `/stop`: **{ph.facts.get('segments_total', 0)}** "
        f"(finais: **{ph.facts.get('segments_final', 0)}**)",
        f"- `transcription_complete` devolvido pelo `/stop`: **{ph.facts.get('transcription_complete', 'n/d')}**",
        "",
        "### Transcript final observado",
        "",
    ]
    transcript = ph.facts.get("transcript") or []
    if transcript:
        lines += ["| Falante | Texto |", "|---------|-------|"]
        for seg in transcript:
            text = seg["text"].replace("|", "\\|")
            lines.append(f"| {seg['speaker']} | {text} |")
    else:
        lines.append("_Nenhum segmento final foi produzido nesta execução._")

    if ph.facts.get("tcc_message"):
        lines += [
            "",
            "## Bloqueio de permissão (TCC)",
            "",
            "O companion nativo saiu com código **2** no preflight de permissão. Mensagem literal:",
            "",
            "```",
            str(ph.facts["tcc_message"]),
            "```",
            "",
            "Para desbloquear: **Ajustes do Sistema → Privacidade e Segurança → Gravação de Tela "
            "e Áudio do Sistema** → habilitar o app de Terminal usado para rodar este script, "
            "reiniciar o Terminal e rodar de novo:",
            "",
            "```bash",
            'cd "/Volumes/Extreme Pro/MYPROJECTS/Transcriptor" && \\',
            "  .venv/bin/python scripts/verify_live_system_audio.py --with-restart-drill",
            "```",
        ]

    lines += [
        "",
        "## Pré-requisito de código (defeito encontrado por esta prova)",
        "",
        "A primeira execução desta prova reprovou com **zero** segmentos do Candidato e expôs "
        "um defeito real no CLI do companion: `activeSources` não era lida depois dos "
        "`append`, e o ARC pode liberar uma variável local no seu **último uso** — não no fim "
        "do escopo. Em build de release isso derrubava o `SCStream` logo após o start, então "
        "a captura anunciava \"active\" e nenhum frame de áudio do sistema chegava ao gateway "
        "(silenciosamente: sem erro, sem queda de conexão). Diagnóstico: a mesma classe de "
        "captura entregou 126 frames em 6 s a um sink simples enquanto o binário entregava 0 "
        "no mesmo instante, na mesma máquina, com o mesmo áudio.",
        "",
        "A correção (`withExtendedLifetime(activeSources)` no laço principal de "
        "`Sources/TarsCompanionCLI/main.swift`) está no commit `365fe20`. "
        "**Reproduzir esta prova exige um `tars-companion` compilado desse commit ou posterior**; "
        "binários anteriores falham nas fases do Candidato.",
        "",
    ]

    # O teto de alegação é uma afirmação positiva: só aparece quando todas as
    # fases executadas passaram. Numa execução com falhas, imprimi-lo diria que
    # a espinha de captura está comprovada — exatamente o que não aconteceu.
    if ph.failed:
        lines += [
            "## Conclusão desta execução",
            "",
            "**Esta execução FALHOU.** As fases marcadas `FAIL` acima não foram satisfeitas, "
            "portanto este documento não sustenta alegação alguma sobre o canal do candidato, "
            "sobre a rotulagem por fonte ou sobre a espinha de captura nativa. Ele registra "
            "somente o que foi observado numa tentativa malsucedida. O teto de alegação desta "
            "prova é deliberadamente omitido: ele vale apenas quando todas as fases executadas "
            "passam.",
            "",
        ]
    else:
        lines += [
            "## Teto de alegação",
            "",
            "> Comprova apenas: espinha de captura nativa funcionando ao vivo na máquina do "
            "proprietário (escopo piloto-solo). Não comprova: piloto G6, Windows, hospedagem, lançamento.",
            "",
        ]

        if ph.blocked:
            lines += [
                "**Ressalva desta execução:** as fases marcadas `BLOQUEADO` não foram executadas por "
                "falta da permissão de Gravação de Tela e Áudio do Sistema neste host de Terminal. "
                "O que está comprovado aqui é: autenticação do gateway por `stream_key` (chave válida "
                "aceita, chave inválida rejeitada), o enquadramento binário do gateway, e a rotulagem "
                "por fonte do canal `microphone` → **Entrevistador** com STT real. O canal do candidato "
                "(`system_audio` → ScreenCaptureKit → **Candidato**) **não** foi comprovado ao vivo.",
                "",
            ]

    EVIDENCE_DOC.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  ✓ {EVIDENCE_DOC.relative_to(REPO_ROOT)}", flush=True)


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Prova ao vivo do canal do candidato (piloto-solo).")
    parser.add_argument(
        "--with-restart-drill",
        action="store_true",
        help="mata o companion com SIGKILL no meio do stream e verifica a recaptura",
    )
    args = parser.parse_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    print("━" * 62)
    print("  T.A.R.S. — Prova ao vivo: canal do candidato (piloto-solo)")
    print("━" * 62)

    ph = Phases()
    backend: subprocess.Popen | None = None
    backend_log: IO[bytes] | None = None
    companion: CompanionRun | None = None
    mic: MicChannel | None = None

    try:
        if not phase_preflight(ph):
            return EXIT_PREFLIGHT

        started = phase_backend(ph)
        if started is None:
            return EXIT_FAILED
        backend, backend_log = started

        created = phase_session(ph)
        if created is None:
            return EXIT_FAILED
        session_id, stream_key = created

        phase_wrong_key(ph, session_id, stream_key)

        voice = str(ph.facts["voice"])
        companion = phase_companion(ph, session_id, stream_key)

        # O canal do entrevistador abre antes da fala do candidato e fica
        # transmitindo até o /stop, espelhando o companion real.
        mic = phase_interviewer_audio(ph, session_id, stream_key, voice)

        if companion is not None:
            phase_candidate_audio(ph, voice)
        else:
            ph.record(
                "Áudio do candidato reproduzido",
                "BLOQUEADO",
                "sem captura de áudio do sistema — reproduzir a frase não provaria nada",
            )

        did_restart = False
        if args.with_restart_drill and companion is not None:
            phase_restart_drill(ph, companion, session_id, stream_key, voice)
            # O relançado é registrado em facts assim que nasce, inclusive quando
            # a fase reprova — só assim o `finally` consegue sempre matá-lo.
            replacement = ph.facts.pop("restart_run", None)
            did_restart = any(r["name"] == "Reinício do companion" and r["status"] == "PASS" for r in ph.rows)
            if isinstance(replacement, CompanionRun):
                companion = replacement  # o processo vivo agora é o relançado
        elif args.with_restart_drill:
            ph.record("Reinício do companion", "BLOQUEADO", "depende da captura de áudio do sistema")

        phase_stop_and_assert(
            ph,
            session_id,
            expect_candidate=companion is not None,
            expect_restart=did_restart,
            mic=mic,
            companion=companion,
        )
        phase_evidence(ph, args)

    finally:
        if mic is not None:
            mic.stop()
        # `restart_run` só sai de facts quando a fase do reinício rodou; se ela
        # levantou no meio, o processo relançado ainda está lá para ser morto.
        for run in (ph.facts.pop("restart_run", None), companion):
            if isinstance(run, CompanionRun):
                run.stop()
        if backend is not None and backend.poll() is None:
            backend.terminate()
            try:
                backend.wait(timeout=15)
            except subprocess.TimeoutExpired:
                backend.kill()
        if backend_log is not None:
            backend_log.close()

    print("\n" + "━" * 62)
    if ph.failed:
        print("✗ PROVA AO VIVO: FALHOU — veja as fases marcadas FAIL acima.")
        result = EXIT_FAILED
    elif ph.blocked:
        print("⏸ PROVA AO VIVO: PARCIAL (BLOQUEADA) — permissão de Gravação de Tela e")
        print("  Áudio do Sistema ausente. Nenhuma asserção falhou; o canal do candidato")
        print("  simplesmente não pôde ser exercitado neste host.")
        result = EXIT_TCC_BLOCKED
    else:
        print("✓ PROVA AO VIVO: PASSOU — canal do candidato comprovado de ponta a ponta.")
        result = EXIT_OK
    print("━" * 62 + "\n")
    return result


if __name__ == "__main__":
    sys.exit(main())
