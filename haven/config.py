"""Runtime configuration, read from environment variables."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("HAVEN_DATA_DIR", ROOT / "data"))
SESSIONS_DIR = DATA_DIR / "sessions"
CLIENTS_FILE = DATA_DIR / "clients.json"
DB_FILE = DATA_DIR / "haven.db"
WEB_DIR = ROOT / "web"

# "demo" runs the rule-based engine with no model; "ollama" uses Ollama; "openai" uses a
# local OpenAI-compatible server (vLLM, llama-server, OpenVINO Model Server) such as on the B70.
LLM_BACKEND = os.environ.get("HAVEN_LLM", "auto")
LLM_URL = os.environ.get("HAVEN_LLM_URL", "http://127.0.0.1:11434")
LLM_MODEL = os.environ.get("HAVEN_MODEL", "qwen2.5:7b")
LLM_TIMEOUT = float(os.environ.get("HAVEN_LLM_TIMEOUT", "180"))
# A 50-minute recording takes far longer than one note.
ASR_TIMEOUT = float(os.environ.get("HAVEN_ASR_TIMEOUT", "900"))

# "faster-whisper" runs Whisper in this process (CPU); "openai" sends the audio to a local
# server with the OpenAI transcription API, such as vLLM serving Whisper on the B70.
ASR_BACKEND = os.environ.get("HAVEN_ASR", "faster-whisper")
ASR_URL = os.environ.get("HAVEN_ASR_URL", "http://127.0.0.1:8001")
ASR_MODEL = os.environ.get("HAVEN_ASR_MODEL", "small" if ASR_BACKEND == "faster-whisper" else "whisper")
# Text given to Whisper before the audio. Empty for the vLLM server: measured on the B70
# with whisper-large-v3-turbo, a prompt made it drop or repeat the last utterance of a clip.
# Unset means the default for the backend (see transcript.asr_prompt).
ASR_PROMPT = os.environ.get("HAVEN_ASR_PROMPT")
# Alignment hybrid (docs/specs/asr-qwen3-evaluation.md §3.1, haven/asr_refine.py). With
# HAVEN_ASR_REFINE=1 Whisper still gives the segments and times, and a second local server
# (vLLM serving Qwen3-ASR on the B70, port 8002) transcribes the whole recording again; its
# text is cut to fit Whisper's segments. If that server fails, Whisper's text is kept.
ASR_REFINE = os.environ.get("HAVEN_ASR_REFINE") == "1"
ASR_REFINE_URL = os.environ.get("HAVEN_ASR_REFINE_URL", "http://127.0.0.1:8002")
ASR_REFINE_MODEL = os.environ.get("HAVEN_ASR_REFINE_MODEL", "qwen3-asr")

# Clinical aids derived from the transcript: the risk-cue scan, checklist and risk gate on
# signing, forced quotes of high-level risk lines in R, score lines, outcome alerts and
# charts, and Case Memory. Off by default (MVP Safety Boundary); the code is kept. Read as
# config.CLINICAL_AIDS at call time, so tests can switch it.
CLINICAL_AIDS = os.environ.get("HAVEN_CLINICAL_AIDS") == "1"

HOST = os.environ.get("HAVEN_HOST", "127.0.0.1")
PORT = int(os.environ.get("HAVEN_PORT", "8765"))

# Clinic network use (one main PC, therapists' laptops or tablets in the rooms). HAVEN_HOST
# other than loopback needs accounts, a certificate and the names the devices use for this
# machine; Haven refuses to start without them. See scripts/add_user.py, scripts/make_cert.py.
TLS_CERT = os.environ.get("HAVEN_TLS_CERT", "")
TLS_KEY = os.environ.get("HAVEN_TLS_KEY", "")
HOSTNAMES = [h.strip() for h in os.environ.get("HAVEN_HOSTNAMES", "").split(",") if h.strip()]
# Accounts and client assignments are managed on the main PC itself (https://localhost:<port>).
# Set to 1 to allow an admin account to manage them from another device on the clinic network.
ADMIN_FROM_NETWORK = os.environ.get("HAVEN_ADMIN_FROM_NETWORK") == "1"

# Background processing (transcription and drafting) runs on this many worker threads.
JOB_WORKERS = max(1, int(os.environ.get("HAVEN_JOB_WORKERS", "2")))

# Original audio (haven/audio.py): kept after transcription so the therapist can listen back,
# removed when the note is signed (HAVEN_AUDIO_DELETE_ON_CONFIRM=0 keeps it until it expires),
# when this many hours have passed, or when the therapist deletes it. 72 hours is a TEST
# setting: it has not been checked against any legal or professional retention rule.
# The note template a draft uses when the request names none (a recap always defaults to QUAL).
# QUAL (單次晤談質化紀錄) leaves the judgement fields to the therapist; SOAP and DAP have the
# model draft A and P. Read at call time (templates.default_id()).
DEFAULT_TEMPLATE = os.environ.get("HAVEN_DEFAULT_TEMPLATE", "QUAL")

# Read at call time (config.AUDIO_TTL_HOURS), so tests can change them.
AUDIO_TTL_HOURS = float(os.environ.get("HAVEN_AUDIO_TTL_HOURS", "72"))
AUDIO_DELETE_ON_CONFIRM = os.environ.get("HAVEN_AUDIO_DELETE_ON_CONFIRM", "1") != "0"
