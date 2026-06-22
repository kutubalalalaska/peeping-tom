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

    # --- local media decode ---
    vision_backend: str = os.environ.get("VISION_BACKEND", "ollama")   # ollama | mock
    ollama_host: str = os.environ.get("OLLAMA_HOST", "http://ollama:11434")
    vision_model: str = os.environ.get("VISION_MODEL", "qwen2.5vl:7b")
    whisper_model: str = os.environ.get("WHISPER_MODEL", "base")
    decode_workers: int = int(os.environ.get("DECODE_WORKERS", "8"))
    decode_max_px: int = int(os.environ.get("DECODE_MAX_PX", "1280"))          # cap longest side before the VLM
    vision_num_predict: int = int(os.environ.get("VISION_NUM_PREDICT", "128"))  # cap caption length (tokens)
    ollama_keep_alive: str = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")        # keep the model warm across calls/jobs

    # --- hosted-tier behaviour ---
    hosted: bool = _b("HOSTED")                # hosted "exhibit" tier — consent + server-side decode
    ephemeral: bool = _b("EPHEMERAL")          # delete raw media + transcript after the read

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
