"""The ONE remote completion core — the only place data crosses the boundary.

Every frontier call in the app goes through `complete()`: one-shot reads, era
reads, synthesis, and (later) fold rounds. It owns, in one place, what used to
be duplicated across two stacks:

  - provider dialects: OpenAI-compatible (OpenRouter/vLLM/…) and Anthropic
  - streaming (SSE) with reasoning capture — events surface as
    on_event(kind∈{"content","reasoning"}, text_so_far); prompt/NOTE semantics
    live in protocol.py, not here
  - retry with backoff EVERYWHERE (GLM over OpenRouter is flaky at long context:
    empty `finish_reason:"error"` bodies and malformed JSON are both transient;
    payment/auth errors are terminal and never retried)
  - the OpenRouter zero-data-retention hint
  - the mock route (caller supplies the canned reply; the stream replay lives
    here so the analyzing UI is exercisable with no endpoint)

Only assembled TEXT is ever sent — never raw media.
"""

import json, re, ssl, time, urllib.error, urllib.request

from .config import settings

# Use certifi's CA bundle when present (transitive dep; fixes macOS-Python's
# missing system-cert issue for host runs). Falls back to the default context.
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()


class NotConfigured(RuntimeError):
    pass


def _post(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=900, context=_SSL_CTX) as r:
        return json.loads(r.read())


def _post_stream(url, payload, headers, on_json):
    """POST a `stream:True` request; feed each SSE `data:` JSON object to on_json."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=900, context=_SSL_CTX) as r:
        for raw in r:                                   # response is line-iterable
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue                                # skip SSE `event:`/keep-alive lines
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                on_json(json.loads(data))
            except json.JSONDecodeError:
                continue                                # ignore malformed/partial chunks


def _content_or_empty(data: dict, where: str) -> str:
    """Assistant text from a non-streaming OpenAI-shape response, NEVER None. A
    reasoning model / safety layer can return `content: null` — log WHY (refusal /
    finish_reason) so an empty result is visible, not silent."""
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = msg.get("content")
    if not content:
        print(f"[{where}] empty content (finish={ch.get('finish_reason')}, "
              f"refusal={str(msg.get('refusal'))[:160]})", flush=True)
        return ""
    return content


def _emit_mock_stream(full: str, on_event) -> None:
    """Replay a canned reply as a (reasoning → content) token stream so the
    analyzing UI is exercisable on the mock route."""
    from . import protocol
    acc = ""
    for n in protocol.MOCK_NOTES:
        acc = f"{acc}\n{n}" if acc else n
        on_event("reasoning", acc)
        time.sleep(0.25)
    body_acc = ""
    for w in full.split(" "):
        body_acc = f"{body_acc} {w}" if body_acc else w
        on_event("content", body_acc)
        time.sleep(0.03)


def _complete_once(system: str, user: str, route, on_event=None) -> str:
    base = route.base_url.rstrip("/")

    if route.provider == "anthropic":
        payload = {"model": route.model, "max_tokens": 4096, "system": system,
                   "messages": [{"role": "user", "content": user}]}
        headers = {"x-api-key": route.api_key, "anthropic-version": "2023-06-01"}
        if on_event is None:
            data = _post(f"{base}/v1/messages", payload, headers)
            return "".join(b.get("text", "") for b in data.get("content", []))
        payload["stream"] = True
        chunks, think = [], []

        def aev(e):
            if e.get("type") != "content_block_delta":
                return
            d = e.get("delta") or {}
            if d.get("type") == "thinking_delta" and d.get("thinking"):
                think.append(d["thinking"]); on_event("reasoning", "".join(think))
            elif d.get("type") == "text_delta" and d.get("text"):
                chunks.append(d["text"]); on_event("content", "".join(chunks))

        _post_stream(f"{base}/v1/messages", payload, headers, aev)
        return "".join(chunks)

    # OpenAI-compatible (OpenRouter / vLLM / …)
    payload = {"model": route.model, "temperature": 0.4,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    if route.zdr:
        # OpenRouter zero-data-retention: restrict routing to no-retention
        # endpoints and exclude data-collecting providers. Best paired with
        # account-wide ZDR at /settings/privacy.
        payload["provider"] = {"zdr": True, "data_collection": "deny"}
    headers = {"Authorization": f"Bearer {route.api_key}"}
    if on_event is None:
        data = _post(f"{base}/chat/completions", payload, headers)
        return _content_or_empty(data, "read")
    payload["stream"] = True
    if settings.stream_reasoning:
        # Ask the provider to include reasoning tokens in the stream (OpenRouter).
        payload["reasoning"] = {"enabled": True}
    chunks, think = [], []

    def oev(e):
        for ch in e.get("choices", []):
            d = ch.get("delta") or {}
            r = d.get("reasoning") or d.get("reasoning_content")
            if r:
                think.append(r); on_event("reasoning", "".join(think))
            if d.get("content"):
                chunks.append(d["content"]); on_event("content", "".join(chunks))

    _post_stream(f"{base}/chat/completions", payload, headers, oev)
    return "".join(chunks)


def _is_terminal_error(exc) -> bool:
    """A failure retrying can't fix: payment/auth/not-found/bad-request. Everything
    else from a flaky upstream (5xx, 429, malformed JSON, timeouts, dropped
    connections) is transient and worth retrying."""
    code = getattr(exc, "code", None)              # urllib.error.HTTPError carries .code
    return code in (400, 401, 402, 403, 404)


def complete(system: str, user: str, route, on_event=None, mock_reply=None,
             attempts: int = 4) -> str:
    """One frontier completion with retry. `on_event(kind, text_so_far)` streams
    tokens (kind "content" | "reasoning"); without it the call blocks. For the
    mock route, `mock_reply(user)` supplies the canned text (and streams it)."""
    if route is None or not route.ready():
        raise NotConfigured(settings.frontier_hint())
    if route.provider == "mock":
        out = (mock_reply or (lambda _u: "mock reply"))(user)
        if on_event:
            _emit_mock_stream(out, on_event)
        return out

    last_exc = out = None
    for attempt in range(attempts):
        try:
            out = _complete_once(system, user, route, on_event)
            if (out or "").strip():
                return out
            reason = "empty result (finish=error)"
        except Exception as e:                     # JSONDecodeError, HTTPError 5xx/429, timeout…
            if _is_terminal_error(e):
                raise                              # 402 payment / 401 auth — don't burn retries
            last_exc, reason = e, f"{type(e).__name__}: {str(e)[:80]}"
        if attempt < attempts - 1:
            print(f"[frontier] {reason} — retry {attempt + 1}/{attempts} after backoff", flush=True)
            time.sleep(4 * (attempt + 1))
    if last_exc is not None and not (out or "").strip():
        raise last_exc                             # exhausted on exceptions — surface the real error
    return out or ""


# --- boot-time auth probe -----------------------------------------------------------

def _http_status(url, headers, timeout=15):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def probe_auth(route):
    """Cheap boot-time auth check. (ok, detail): True = verified, False = rejected
    (bad/missing key), None = couldn't verify (don't cry wolf). Never raises —
    this is what catches a bad API key before a user's read fails cryptically."""
    if route.provider == "mock":
        return (None, "mock route — no auth")
    if not route.ready():
        return (False, "route not configured (missing base_url/model)")
    base = route.base_url.rstrip("/")
    if route.provider == "anthropic":
        code = _http_status(base + "/models",
                            {"x-api-key": route.api_key, "anthropic-version": "2023-06-01"})
    elif "openrouter.ai" in base:                       # OpenRouter: /key is a free, auth-gated probe
        code = _http_status(base + "/key", {"Authorization": f"Bearer {route.api_key}"})
    else:                                               # generic OpenAI-compatible (vLLM / self-host)
        code = _http_status(base + "/models", {"Authorization": f"Bearer {route.api_key}"})
    if code == 200:
        return (True, "auth ok")
    if code in (401, 403):
        return (False, f"auth rejected (HTTP {code}) — check the API key")
    if code is None:
        return (None, "could not reach the endpoint")
    return (None, f"unexpected probe status HTTP {code}")
