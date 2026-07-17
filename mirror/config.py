"""Runtime configuration.

The environment is deliberately SMALL (see .env.example): keys and hosts, the
deployment tier, and the safety rails. Everything that used to be an env tuning
knob lives in code:

  - DecodeProfile (DECODE_PROFILE=gpu|cpu) — local decode tuning per hardware
    class: which VLM/Whisper models, image sizes, worker counts, timeouts.
  - Mode ("fast" | "deep") — the read pipeline's envelopes (request budgets,
    fold-in cadence). Consumed by the pipeline orchestrator.

The read runs through ONE remote route (ROUTE_A_* — the names are kept from the
multi-route era so existing .env files stay valid). ROUTE_A_PROVIDER=mock runs
the whole flow with no endpoint and no models (flow tests).

Legacy env names from the pre-rebuild config are detected and logged as ignored
at import, so a stale .env fails loudly into the logs instead of silently.
"""

import os
from dataclasses import dataclass


def _b(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes", "on")


# --- the read route -----------------------------------------------------------

@dataclass(frozen=True)
class Route:
    """The read backend. Only the assembled TEXT transcript ever reaches it."""
    id: str
    provider: str           # openai (OpenAI-compatible) | anthropic | mock
    base_url: str
    api_key: str
    model: str
    zdr: bool = False       # ask OpenRouter for zero-data-retention endpoints only

    def ready(self) -> bool:
        return self.provider == "mock" or bool(self.base_url and self.model)

    def public(self) -> dict:
        """The view /api/config may show — no secrets."""
        return {
            "id": self.id,
            "kind": "mock" if self.provider == "mock" else "managed_api",
            "model": self.model,
            "label": "Demo read" if self.provider == "mock" else "Maximum insight",
            "third_party": self.provider != "mock",
            "zero_retention": self.zdr,
            "expect_cold_start": False,
            "ready": self.ready(),
        }


def _build_route():
    provider = os.environ.get("ROUTE_A_PROVIDER", "openai")
    if provider == "mock":
        return Route(id="mock", provider="mock", base_url="", api_key="", model="")
    base = os.environ.get("ROUTE_A_BASE_URL", "")
    model = os.environ.get("ROUTE_A_MODEL", "")
    if not (base and model):
        return None
    return Route(id="managed-api", provider=provider, base_url=base,
                 api_key=os.environ.get("ROUTE_A_API_KEY", ""), model=model,
                 zdr=_b("ROUTE_A_ZDR"))


_ROUTE = _build_route()


# --- decode profiles (DECODE_PROFILE) ------------------------------------------

@dataclass(frozen=True)
class DecodeProfile:
    """Local-decode tuning for one hardware class. Chosen once per deployment;
    VISION_MODEL / WHISPER_MODEL / DECODE_WORKERS env vars override single fields
    when a box really needs it."""
    vision_model: str        # deep-pass VLM
    vision_model_fast: str   # cheap-all pass VLM
    px_fast: int             # longest image side, cheap pass
    px_deep: int             # longest image side, deep pass
    workers: int             # VLM thread pool
    vlm_timeout: int         # per-call timeout (s)
    keep_alive: str          # how long Ollama keeps the VLM warm
    whisper: str             # pass-1 ASR model
    escalate: str            # tiered-ASR escalation model ("" disables)
    escalate_max_s: int      # cap on total re-run audio seconds


PROFILES = {
    # A machine with a real GPU (or Apple Silicon host Ollama): big models, big images.
    "gpu": DecodeProfile("qwen2.5vl:7b", "qwen2.5vl:3b", 768, 1280, 8, 90, "30m",
                         "base", "large-v3-turbo", 1800),
    # A CPU VPS (the hosted exhibit): the 3B for both passes, small images, few
    # workers, generous timeouts, warm all day. Matches the tuning validated live.
    "cpu": DecodeProfile("qwen2.5vl:3b", "qwen2.5vl:3b", 384, 512, 2, 180, "24h",
                         "base", "large-v3-turbo", 900),
}

_PROFILE_NAME = os.environ.get("DECODE_PROFILE", "gpu").lower()
if _PROFILE_NAME not in PROFILES:
    print(f"[config] unknown DECODE_PROFILE={_PROFILE_NAME!r} — using 'gpu'", flush=True)
    _PROFILE_NAME = "gpu"
_PROFILE = PROFILES[_PROFILE_NAME]


# --- read modes (the pipeline's envelopes) --------------------------------------

@dataclass(frozen=True)
class Mode:
    """Per-mode envelope for the read pipeline. The model REASONS about which
    media to decode; these are the outer bounds it works within, not micro-caps."""
    request_rounds: int = 1          # media-request rounds after the first read (fast)
    max_request_items: int = 24      # total media items the read may have decoded
    max_request_audio_s: int = 600   # total requested audio seconds
    decode_wall_s: int = 480         # wall-clock budget for fulfilling one request round
    era_request_items: int = 6       # per-era request cap (map-reduce)
    era_request_audio_s: int = 240
    fold_min_items: int = 40         # deep: fold a round once this much new evidence
    fold_min_interval_s: int = 90    # deep: or this much time + fold_trickle_items
    fold_trickle_items: int = 8
    fold_max_per_round: int = 150    # deep: split bigger deltas across rounds
    era_reread_threshold: int = 10   # deep tier-3: re-read an era that gained this many


MODES = {
    "fast": Mode(),                  # text-first; decode only what the read asks for
    "deep": Mode(request_rounds=0),  # full decode in parallel; evidence folds in
}


# --- stale-env detection ---------------------------------------------------------

_LEGACY_ENV = (
    "FRONTIER_PROVIDER", "FRONTIER_BASE_URL", "FRONTIER_API_KEY", "FRONTIER_MODEL",
    "FRONTIER_KIND", "FRONTIER_THIRD_PARTY", "READ_DEFAULT_ROUTE",
    "ROUTE_B_PROVIDER", "ROUTE_B_BASE_URL", "ROUTE_B_API_KEY", "ROUTE_B_MODEL",
    "ROUTE_A_LABEL", "ROUTE_A_ID", "ROUTE_A_KIND", "ROUTE_A_THIRD_PARTY", "ROUTE_A_COLD_START",
    "ITERATIVE_DISCOVERY", "MAX_INSPECT_ROUNDS", "MAX_INSPECT_IMAGES",
    "MAX_INSPECT_IMAGES_TOTAL", "IMAGES_PER_ERA", "DEEP_SELECT_K",
    "COMPACT_TRANSCRIPT", "CHUNK_FILL", "READ_SAFETY", "READ_RESERVE_TOKENS",
    "VISION_MODEL_FAST", "DECODE_MAX_PX", "DECODE_MAX_PX_FAST",
    "VISION_NUM_PREDICT", "VISION_TIMEOUT", "OLLAMA_KEEP_ALIVE",
    "WHISPER_LANGUAGE", "WHISPER_BEAM", "WHISPER_ESCALATE_MODEL",
    "WHISPER_ESCALATE_MAX_SECONDS", "WHISPER_ESCALATE_LOGPROB",
    "TRANSCRIBE_VIDEO", "VIDEO_MAX_MB", "MARK_EXPLICIT", "EXPLICIT_MARKER",
    "NSFW_THRESHOLD", "NSFW_REQUIRED", "SESSION_COOKIE",
)
for _name in sorted(set(_LEGACY_ENV) & set(os.environ)):
    print(f"[config] ignored legacy env {_name} — this knob now lives in code "
          f"(DecodeProfile/Mode presets; see mirror/config.py)", flush=True)


@dataclass(frozen=True)
class Settings:
    # --- the read ---
    routes: tuple = (_ROUTE,) if _ROUTE else ()
    # Stream the model's real reasoning tokens to the live "thinking" view
    # (OpenRouter `reasoning`; probe-confirmed for GLM-5.2 on the live endpoint).
    stream_reasoning: bool = _b("READ_STREAM_REASONING")
    # The context window the size gate plans against. 262k (not the model's full
    # window): on OpenRouter it routes GLM onto the reliable fp8 providers instead
    # of the flaky >=600k-context fp4 ones — validated on the 4.7GB test chat.
    read_context_tokens: int = int(os.environ.get("READ_CONTEXT_TOKENS", "262144"))
    read_reserve_tokens: int = 8000     # held back for system prompt + wrapper + output
    read_safety: float = 0.9            # margin on the (approximate) token estimate
    chunk_fill: float = 0.6             # fraction of usable window per map-reduce era

    # --- local media decode ---
    vision_backend: str = os.environ.get("VISION_BACKEND", "ollama")   # ollama | mock
    ollama_host: str = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
    decode_profile: str = _PROFILE_NAME
    vision_model: str = os.environ.get("VISION_MODEL", "") or _PROFILE.vision_model
    vision_model_fast: str = _PROFILE.vision_model_fast
    decode_max_px: int = _PROFILE.px_deep
    decode_max_px_fast: int = _PROFILE.px_fast
    decode_workers: int = int(os.environ.get("DECODE_WORKERS", "0") or 0) or _PROFILE.workers
    vision_timeout: int = _PROFILE.vlm_timeout
    vision_num_predict: int = 128       # cap caption length (tokens)
    ollama_keep_alive: str = _PROFILE.keep_alive
    whisper_model: str = os.environ.get("WHISPER_MODEL", "") or _PROFILE.whisper
    whisper_language: str = ""          # "" = detect once from the chat's text
    whisper_beam: int = 5
    whisper_escalate_model: str = _PROFILE.escalate
    whisper_escalate_max_s: int = _PROFILE.escalate_max_s
    whisper_escalate_logprob: float = -0.8
    transcribe_video: bool = True       # round notes always; shared clips under video_max_mb
    video_max_mb: int = 25

    # --- explicit content: detect LOCALLY (NudeNet, before captioning), carry a
    # neutral marker — nothing intimate ever crosses the boundary. Fail-closed on
    # the hosted tier (no knob: hosted implies required). ---
    mark_explicit: bool = True
    explicit_marker: str = "intimate/explicit image"
    nsfw_threshold: float = 0.5

    # One-window transcripts stay in the full human-readable form; map-reduce
    # eras always assemble compact. (Constant — was the COMPACT_TRANSCRIPT knob.)
    compact_transcript: bool = False

    # --- deployment tier ---
    hosted: bool = _b("HOSTED")         # hosted "exhibit": consent + rate moat + TTL
    ephemeral: bool = _b("EPHEMERAL")   # delete raw media + transcript after the read

    # --- hosted tier: self-destruct + garbage sweep ---
    read_ttl_seconds: int = int(os.environ.get("READ_TTL_SECONDS", "600"))
    max_job_age_seconds: int = int(os.environ.get("MAX_JOB_AGE_SECONDS", "3600"))
    purge_interval_seconds: int = int(os.environ.get("PURGE_INTERVAL_SECONDS", "120"))

    # --- hosted tier: abuse moat (no login, no PII) ---
    rate_window_seconds: int = int(os.environ.get("RATE_WINDOW_SECONDS", "86400"))
    rate_max_per_session: int = int(os.environ.get("RATE_MAX_PER_SESSION", "5"))
    rate_max_per_ip: int = int(os.environ.get("RATE_MAX_PER_IP", "20"))
    session_cookie: str = "mirror_sid"
    cookie_secure: bool = _b("COOKIE_SECURE")

    # --- ops: admin page + problem alerts (optional; unset = feature off) ---
    admin_user: str = os.environ.get("ADMIN_USER", "admin")
    admin_pass: str = os.environ.get("ADMIN_PASS", "")      # empty = /admin answers 404
    telegram_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    telegram_chat: str = os.environ.get("TELEGRAM_CHAT_ID", "")
    # Also ping on normal activity (new upload, finished read) — launch-watching.
    telegram_activity: bool = _b("TELEGRAM_ACTIVITY", "1")

    # --- upload guard + paths ---
    # v0 decision (2026-07-10): sized so any accepted chat reads in ONE coherent
    # pass — the client-side slicer enforces the token axis, this caps the bytes.
    max_upload_mb: int = int(os.environ.get("MAX_UPLOAD_MB", "1536"))
    data_dir: str = os.environ.get("DATA_DIR", "/data")
    web_dir: str = os.environ.get("WEB_DIR", "")

    @property
    def nsfw_required(self) -> bool:
        """If the NSFW detector can't load: fail CLOSED on the hosted exhibit
        (captions skipped), fail open (but loud) on self-host."""
        return self.hosted

    def frontier_ready(self) -> bool:
        return any(r.ready() for r in self.routes)

    def default_route_id(self):
        return self.routes[0].id if self.routes else None

    def route(self, route_id: str = None):
        """The route (by id or default). None if nothing is configured."""
        if route_id:
            return next((r for r in self.routes if r.id == route_id), None)
        return self.routes[0] if self.routes else None

    def public_routes(self) -> list:
        return [r.public() for r in self.routes]

    def frontier_hint(self) -> str:
        return ("The read needs a frontier model. Set ROUTE_A_BASE_URL + ROUTE_A_MODEL "
                "+ ROUTE_A_API_KEY (e.g. OpenRouter), or ROUTE_A_PROVIDER=mock to try "
                "the flow with no endpoint. See .env.example.")


settings = Settings()
