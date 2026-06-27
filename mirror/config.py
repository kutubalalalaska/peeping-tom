"""Runtime configuration, all from environment variables (see .env.example).

The read can run through one of several **routes** (read backends). The hosted
"exhibit" tier exposes more than one and lets the *user* pick:

  - Track A — "managed_api": a managed open-weight inference API (e.g. OpenRouter
    with zero-data-retention) serving a true frontier-open model. Maximum insight;
    the assembled TEXT transcript transits a third party (mitigated by ZDR).
  - Track B — "self_host": our own VPS running an open model behind an
    OpenAI-compatible server (vLLM/SGLang). Privacy-pure — nothing leaves our
    controlled stack — at the cost of a smaller model and a possible cold start.

A single legacy `FRONTIER_*` block still works and becomes one route, so the
self-host tier and the mock compose keep running unchanged.
"""

import os
from dataclasses import dataclass


def _b(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Route:
    """One read backend the user can be routed to."""
    id: str
    kind: str               # managed_api | self_host | mock
    provider: str           # openai (OpenAI-compatible) | anthropic | mock
    base_url: str
    api_key: str
    model: str
    label: str = ""         # fallback label; UI copy (narrative thread) may override
    zdr: bool = False        # send a zero-data-retention hint in the payload (OpenRouter)
    third_party: bool = True   # does the assembled TEXT reach a third party?
    expect_cold_start: bool = False   # first read may wait for a cold GPU to spin up

    def ready(self) -> bool:
        if self.provider == "mock":
            return True
        return bool(self.base_url and self.model)

    def public(self) -> dict:
        """The view the frontend/`/api/config` may show — no secrets."""
        return {
            "id": self.id,
            "kind": self.kind,
            "model": self.model,
            "label": self.label,
            "third_party": self.third_party,     # text crosses to a third party?
            "zero_retention": self.zdr,           # provider asked not to retain it
            "expect_cold_start": self.expect_cold_start,
            "ready": self.ready(),
        }


def _route_from_env(prefix, default_id, default_kind, default_label,
                    default_third_party, default_cold):
    """Build a Route from a `<PREFIX>_*` env group, or None if unconfigured."""
    provider = os.environ.get(f"{prefix}_PROVIDER", "openai")
    base = os.environ.get(f"{prefix}_BASE_URL", "")
    model = os.environ.get(f"{prefix}_MODEL", "")
    if provider != "mock" and not (base and model):
        return None
    return Route(
        id=os.environ.get(f"{prefix}_ID", default_id),
        kind=os.environ.get(f"{prefix}_KIND", default_kind),
        provider=provider,
        base_url=base,
        api_key=os.environ.get(f"{prefix}_API_KEY", ""),
        model=model,
        label=os.environ.get(f"{prefix}_LABEL", default_label),
        zdr=_b(f"{prefix}_ZDR"),
        third_party=_b(f"{prefix}_THIRD_PARTY", "1" if default_third_party else "0"),
        expect_cold_start=_b(f"{prefix}_COLD_START", "1" if default_cold else "0"),
    )


def _build_routes() -> tuple:
    routes = []
    # Track A — managed API (OpenRouter / DeepInfra / …): frontier-open, third-party.
    a = _route_from_env("ROUTE_A", "managed-api", "managed_api",
                         "Maximum insight", default_third_party=True, default_cold=False)
    if a:
        routes.append(a)
    # Track B — self-host on our VPS: privacy-pure, may cold-start.
    b = _route_from_env("ROUTE_B", "self-host", "self_host",
                        "Maximum privacy", default_third_party=False, default_cold=True)
    if b:
        routes.append(b)
    if routes:
        return tuple(routes)

    # --- legacy single-route fallback (FRONTIER_*) — self-host & mock tiers ---
    provider = os.environ.get("FRONTIER_PROVIDER", "openai")
    if provider == "mock":
        return (Route(id="mock", kind="mock", provider="mock", base_url="", api_key="",
                      model="", label="Demo read", third_party=False),)
    base = os.environ.get("FRONTIER_BASE_URL", "")
    model = os.environ.get("FRONTIER_MODEL", "")
    if base and model:
        return (Route(id="default", kind=os.environ.get("FRONTIER_KIND", "self_host"),
                      provider=provider, base_url=base,
                      api_key=os.environ.get("FRONTIER_API_KEY", ""), model=model,
                      label="The read", third_party=_b("FRONTIER_THIRD_PARTY")),)
    return ()


_ROUTES = _build_routes()


@dataclass(frozen=True)
class Settings:
    # --- the read: one or more selectable routes (see _build_routes / .env.example) ---
    routes: tuple = _ROUTES
    # Ask managed APIs to stream reasoning tokens (OpenRouter `reasoning`) so the
    # analysis screen can show the model's real chain-of-thought. Off by default
    # until confirmed on the live endpoint (scripts/probe_reasoning.py); the NOTE
    # working-line preamble is the always-on fallback "thinking" source.
    stream_reasoning: bool = _b("READ_STREAM_REASONING")

    # --- local media decode ---
    vision_backend: str = os.environ.get("VISION_BACKEND", "ollama")   # ollama | mock
    ollama_host: str = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
    vision_model: str = os.environ.get("VISION_MODEL", "qwen2.5vl:7b")
    whisper_model: str = os.environ.get("WHISPER_MODEL", "base")
    # Whisper quality levers. language: "" = auto-detect from the chat's TEXT once and apply to
    # all clips (general, per-chat — fixes short-clip misdetect without assuming an audience);
    # "auto" = Whisper's per-clip detection; or a code like "ru" to force. beam_size>1 = better
    # accuracy (1 is greedy). (VAD + condition_on_previous_text=False are applied in code — they
    # cut hallucination-on-silence and repetition loops, language-agnostic.)
    whisper_language: str = os.environ.get("WHISPER_LANGUAGE", "")     # "" = detect-from-corpus
    whisper_beam: int = int(os.environ.get("WHISPER_BEAM", "5"))
    # Transcribe the AUDIO of video messages (round video notes) — many people use them as a
    # primary channel, so their speech is high-signal. Round notes are always transcribed;
    # larger shared clips only if under video_max_mb (avoid transcribing long movies). The
    # visual frames are a separate, deep-pass concern; this is just the speech.
    transcribe_video: bool = _b("TRANSCRIBE_VIDEO", "1")
    video_max_mb: int = int(os.environ.get("VIDEO_MAX_MB", "25"))
    decode_workers: int = int(os.environ.get("DECODE_WORKERS", "8"))
    decode_max_px: int = int(os.environ.get("DECODE_MAX_PX", "1280"))          # cap longest side (deep pass)
    vision_num_predict: int = int(os.environ.get("VISION_NUM_PREDICT", "128"))  # cap caption length (tokens)
    ollama_keep_alive: str = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")        # keep the model warm across calls/jobs
    vision_timeout: int = int(os.environ.get("VISION_TIMEOUT", "90"))          # per-VLM-call timeout (s) — bounds a hung image
    # --- two-pass decode: cheap label-all on a small VLM, deep 7B only on frontier-picked images ---
    vision_model_fast: str = os.environ.get("VISION_MODEL_FAST", "")          # small VLM for the cheap-all pass (e.g. qwen2.5vl:3b); blank = same as vision_model
    decode_max_px_fast: int = int(os.environ.get("DECODE_MAX_PX_FAST", "768")) # smaller cap for the cheap-all pass
    deep_select_k: int = int(os.environ.get("DEEP_SELECT_K", "12"))            # per-round image-pick cap (also the single-round cap)
    # --- explicit content (adult/legal): detect LOCALLY, carry a neutral marker ---
    # When an image is flagged explicit (nudity/sexual content), the transcript carries
    # a neutral marker instead of a graphic caption — so nothing intimate crosses the
    # privacy boundary. The raw image stays local (receipts unaffected). The *fact* of
    # an intimate image at a charged moment is the behavioural signal the read needs (a
    # non-verbal act), not the anatomy — so the read loses nothing it can use. Detection
    # rides the existing blind VLM classification (a dedicated local NSFW classifier is
    # the planned hardening). NOTE: this is the ADULT/LEGAL case only — CSAM is a
    # separate, deferred concern and this flag does NOT attempt to detect it.
    mark_explicit: bool = _b("MARK_EXPLICIT", "1")            # on by default
    explicit_marker: str = os.environ.get("EXPLICIT_MARKER", "intimate/explicit image")
    # The explicit gate is a DEDICATED local NSFW detector (NudeNet) that runs BEFORE
    # captioning — the VLM's self-report can't be trusted (it captions nudes but answers
    # EXPLICIT=no). nsfw_threshold: detector score to count as exposed (lower = stricter,
    # biased for recall). nsfw_required: if the detector can't load, fail CLOSED (skip all
    # captions) instead of degrading to the unreliable VLM flag — set on the hosted exhibit.
    nsfw_threshold: float = float(os.environ.get("NSFW_THRESHOLD", "0.5"))
    nsfw_required: bool = _b("NSFW_REQUIRED")
    # --- scaling: lossless transcript compression (SCALING.md Stage 2) ---
    # Day-grouped dates + short sender tokens (legend in-band) → ~30-40% fewer tokens,
    # #ids preserved. Off by default (existing one-shot behaviour unchanged); turn on
    # for large corpora / the big-chat path. The size gate (below) also auto-uses it.
    compact_transcript: bool = _b("COMPACT_TRANSCRIPT")

    # --- scaling: the size gate (SCALING.md Stage 1) ---
    # When the (language-aware) token estimate exceeds the usable window, the read is
    # done as a chronological MAP-REDUCE instead of one-shot. read_context_tokens is the
    # model's window; default 1M (GLM-5.2). Lower it to force/ test chunking. The reserve
    # holds back room for the system prompt + wrapper + output.
    read_context_tokens: int = int(os.environ.get("READ_CONTEXT_TOKENS", "1000000"))
    read_reserve_tokens: int = int(os.environ.get("READ_RESERVE_TOKENS", "8000"))
    read_safety: float = float(os.environ.get("READ_SAFETY", "0.9"))   # margin on usable (token est is approximate)
    chunk_fill: float = float(os.environ.get("CHUNK_FILL", "0.6"))     # fraction of usable per map-reduce chunk
    # Per-era image deepening (SCALING.md Stage 4): in map-reduce + iterative mode each era
    # may INSPECT up to images_per_era of its own images (effort follows signal, distributed
    # across eras instead of one flat global cap), bounded overall by max_inspect_images_total.
    images_per_era: int = int(os.environ.get("IMAGES_PER_ERA", "6"))
    max_inspect_images_total: int = int(os.environ.get("MAX_INSPECT_IMAGES_TOTAL", "64"))

    # --- iterative discovery: text+audio first, then the frontier requests images in capped rounds ---
    iterative_discovery: bool = _b("ITERATIVE_DISCOVERY")                     # off = cheap-all up front (1 round); on = text-first, multi-round
    max_inspect_rounds: int = int(os.environ.get("MAX_INSPECT_ROUNDS", "3"))  # cap on frontier image-request rounds
    max_inspect_images: int = int(os.environ.get("MAX_INSPECT_IMAGES", "24")) # cap on total images deep-analyzed across all rounds

    # --- hosted-tier behaviour ---
    hosted: bool = _b("HOSTED")                # hosted "exhibit" tier — consent + server-side decode
    ephemeral: bool = _b("EPHEMERAL")          # delete raw media + transcript after the read

    # --- hosted-tier: self-destruct (TTL) ---
    # The read self-destructs READ_TTL_SECONDS after it is READY (state=done) — the
    # countdown the result page shows. Unfinished/abandoned jobs ("garbage") are swept
    # separately: anything that never reached `done` is purged once older than
    # MAX_JOB_AGE_SECONDS (kept well above worst-case CPU decode so a slow job is never
    # killed mid-flight). The in-process sweeper runs every PURGE_INTERVAL_SECONDS;
    # scripts/purge.py does the same for a real system cron.
    read_ttl_seconds: int = int(os.environ.get("READ_TTL_SECONDS", "600"))          # 10 min after the read is ready
    max_job_age_seconds: int = int(os.environ.get("MAX_JOB_AGE_SECONDS", "3600"))   # garbage sweep for never-finished jobs
    purge_interval_seconds: int = int(os.environ.get("PURGE_INTERVAL_SECONDS", "120"))

    # --- hosted-tier: abuse moat (no login, no PII) ---
    # Cap reads per ephemeral cookie-session and per client IP over a rolling window.
    # The cookie cap deters casual re-runs (incognito/clearing cookies resets it); the
    # IP cap + a CDN in front are the real teeth. Counted by scanning the job store, so
    # there is nothing extra to persist and it stays inspectable.
    rate_window_seconds: int = int(os.environ.get("RATE_WINDOW_SECONDS", "86400"))  # 24h
    rate_max_per_session: int = int(os.environ.get("RATE_MAX_PER_SESSION", "5"))
    rate_max_per_ip: int = int(os.environ.get("RATE_MAX_PER_IP", "20"))
    session_cookie: str = os.environ.get("SESSION_COOKIE", "mirror_sid")
    cookie_secure: bool = _b("COOKIE_SECURE")  # set in prod (HTTPS) so the cookie is Secure

    data_dir: str = os.environ.get("DATA_DIR", "/data")
    web_dir: str = os.environ.get("WEB_DIR", "")   # built React SPA; empty -> placeholder pages

    def frontier_ready(self) -> bool:
        """True if at least one route can perform a read."""
        return any(r.ready() for r in self.routes)

    def default_route_id(self):
        """READ_DEFAULT_ROUTE if set & valid, else the first ready route, else first."""
        want = os.environ.get("READ_DEFAULT_ROUTE", "")
        if want and any(r.id == want for r in self.routes):
            return want
        for r in self.routes:
            if r.ready():
                return r.id
        return self.routes[0].id if self.routes else None

    def route(self, route_id: str = None):
        """Resolve a route by id; with no id, return the default route. None if absent."""
        if route_id:
            return next((r for r in self.routes if r.id == route_id), None)
        did = self.default_route_id()
        return next((r for r in self.routes if r.id == did), None)

    def public_routes(self) -> list:
        return [r.public() for r in self.routes]

    def frontier_hint(self) -> str:
        return ("The read needs a frontier model. Configure a route — set ROUTE_A_* "
                "(a managed API like OpenRouter) and/or ROUTE_B_* (your VPS), or the "
                "legacy FRONTIER_BASE_URL/FRONTIER_MODEL. Or FRONTIER_PROVIDER=mock to "
                "try the flow. See .env.example / READ_ROUTES.md.")


settings = Settings()
