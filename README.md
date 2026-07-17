# peeping tom — drop 001

Upload a chat history. A frontier model reads it whole — the patterns, the arc,
the things you stopped noticing — and hands the reading back **with receipts**:
every claim cited to the actual messages it's built on.

The catch in every other version of this idea is that to be read that deeply,
you'd have to hand someone your rawest data. This project exists to refuse that
trade:

> **All perception happens on hardware you control. Photos, voice notes and
> videos are decoded into text locally, by models that never see the
> conversation. One assembled text transcript is the only thing that ever
> crosses to the reading model — and you can inspect it, byte for byte.**

Two ways to use it:

- **Self-host (the real thing):** `docker compose up` on your own machine.
  Nothing leaves it except the one call you yourself configure.
- **Hosted demo:** [peeping-tom.com](https://peeping-tom.com) — try it without
  installing anything. Deliberately ephemeral: reads self-destruct, nothing
  identifies you. A front door meant to convince you to self-host.

---

## privacy policy / how your data is handled

This section is the product. Every claim links to the code that enforces it —
if a promise here doesn't map to running code, that's a bug; file an issue.

### the one rule

Raw media — photos, voice notes, videos, stickers — **never leaves the machine
that decodes it**. Not to the reading model, not to any third party. Media is
turned into text (blind captions, speech transcripts) by local models
([`mirror/decode.py`](mirror/decode.py), Ollama + faster-whisper), and only the
assembled, `#id`-tagged **text** transcript crosses to exactly one configured
endpoint ([`mirror/provider.py`](mirror/provider.py)).

You don't have to take that on faith:
`GET /api/jobs/{id}/transcript` shows **the exact text that crossed the
boundary** ([`mirror/server.py`](mirror/server.py)), and the result page's
transparency panel (`/api/jobs/{id}/retained`) shows what is held at any
moment. A delete button (`DELETE /api/jobs/{id}`) removes everything for a job,
immediately ([`mirror/jobs.py`](mirror/jobs.py)).

### claims → code

| claim | enforced in |
|---|---|
| media decoded locally; only text crosses | [`mirror/pipeline.py`](mirror/pipeline.py), [`mirror/decode.py`](mirror/decode.py), [`mirror/provider.py`](mirror/provider.py) |
| the captioner is *blind* — it never sees your messages, so a matching caption is independent evidence, not suggestion | caption prompts in [`mirror/decode.py`](mirror/decode.py) carry only the image |
| intimate/explicit images are detected locally (NudeNet, before any captioning) and cross only as a neutral `[intimate/explicit image]` marker — a graphic description is never even generated | [`mirror/decode.py`](mirror/decode.py) (`is_explicit_image`); fail-closed on the hosted tier ([`mirror/config.py`](mirror/config.py) `nsfw_required`) |
| citations are receipts, not decoration — invented ids are stripped server-side and the count is disclosed in the result | [`mirror/protocol.py`](mirror/protocol.py) (`validate_citations`), [`mirror/pipeline.py`](mirror/pipeline.py) |
| too-big exports are cut down **in your browser** — the server never receives what you didn't choose to share | [`frontend/src/lib/slicer.ts`](frontend/src/lib/slicer.ts) |
| you can see and delete everything held for a job | [`mirror/jobs.py`](mirror/jobs.py), [`mirror/server.py`](mirror/server.py) |

### if you self-host

Everything runs in your Docker stack: the app, Ollama (vision), Whisper (ASR).
Job data lives in a local volume (`/data`); deleting the volume deletes
everything. Set `EPHEMERAL=1` and raw media + transcript are deleted the moment
the read finishes ([`mirror/jobs.py`](mirror/jobs.py) `delete_raw`).

Network calls a self-hosted instance makes — the complete list:

1. **The read**: the text transcript goes to the endpoint *you* configure in
   `.env` (`ROUTE_A_*` — any OpenAI-compatible or Anthropic-style API, including
   one on your own hardware; your key, your choice of counterparty).
   `ROUTE_A_PROVIDER=mock` runs the whole flow with no endpoint at all.
2. **Model downloads on first run**: Ollama pulls the vision model; Whisper
   models come from Hugging Face. Both cache locally.

That's it. **No telemetry, no phoning home, ever.** The ops features in this
repo (admin page, Telegram alerts, activity pings) are for the *operator of an
instance* and are **off unless you configure them** — the admin page literally
404s without `ADMIN_PASS`, and alerts go to *your* bot, not to us
([`mirror/admin.py`](mirror/admin.py), [`mirror/alerts.py`](mirror/alerts.py)).

### if you use the hosted demo (peeping-tom.com)

The demo deliberately trades some purity for a zero-install front door. Here is
exactly what that trade is, so you can decide with open eyes:

**What leaves your device:** the export you upload (after the in-browser slicer
minimized it, if it was large). It is decoded on our VPS — the same local-only
pipeline, just on our hardware instead of yours.

**What crosses to a third party:** the assembled text transcript, sent to
[GLM-5.2](https://openrouter.ai) via OpenRouter with **zero-data-retention**
endpoints and `data_collection: deny` requested on every single call
([`mirror/provider.py`](mirror/provider.py)), plus ZDR enabled account-wide. That is a
contractual promise by the providers, not physics — if that residual trust is
unacceptable to you, self-host.

**How long we hold anything:**

- A finished read **self-destructs 20 minutes** after it's ready — a live
  countdown runs on the result page, then the whole job is deleted
  ([`mirror/jobs.py`](mirror/jobs.py) `purge_expired`).
- Unfinished/abandoned jobs are swept within hours.
- The delete button works any time before that, and returns a receipt.

**Who you are to us: nobody.** No accounts, no emails, no names. An opaque
random cookie (`mirror_sid`) exists solely to enforce fair-use caps (a few
reads per day per session/IP) — it is never tied to identity and expires on its
own ([`mirror/server.py`](mirror/server.py)).

**What we DO record — full disclosure:** an anonymous, technical operations
log ([`mirror/events.py`](mirror/events.py)): visit *counts*, whether uploads
succeeded or were refused (and why), read durations and outcomes. Each entry is
a bare fact like `{"event": "visit"}` or
`{"event": "job_done", "seconds": 312}` — **it never contains IP addresses,
user agents, session ids, names, or any content from your chat.** We watch the
service, not you. This log is what lets us see that things run smoothly, and it
is exactly as anonymous as this paragraph claims — the file that writes it is
under sixty lines, read it.

**Consent line:** the demo requires you to confirm the conversation is your
own and lawful. Upload only chats you have the right to share.

### the honest limits

A text transcript of your private conversation is still sensitive data. This
design minimizes *what* crosses and *how long anything lives* — it cannot make
the crossing harmless. The reading model's operator (OpenRouter and the model
provider, under ZDR) is a party you are trusting with text. If a read isn't
worth that for a given chat: don't upload that chat, or self-host and point
`ROUTE_A_*` at an endpoint you trust — including one on your own GPU.

---

## how it works

    upload → LOCAL decode → #id-tagged transcript → ONE frontier read → cited result

- **Sources:** WhatsApp export `.zip`, Telegram Desktop JSON
  ([`mirror/ingest.py`](mirror/ingest.py)).
- **Two read modes**, chosen per upload ([`mirror/pipeline.py`](mirror/pipeline.py)):
  - **fast** (default): the model reads text-first with a metadata manifest of
    the media (`[voice 3:47 — undecoded]`), *reasons about which media would
    change the read*, and requests them — its reasons surface live in the UI.
    Only the requested items are decoded.
  - **deep**: the entire corpus decodes in parallel while the read runs;
    evidence folds into a working draft, then a final re-grounding pass.
- **Giant chats** run through chronological map-reduce (era reads → synthesis,
  [`mirror/budget.py`](mirror/budget.py)); oversized exports are sliced to a
  date window in the browser before upload.
- **The reading itself** is shaped by [`mirror/soul.md`](mirror/soul.md): a
  defining phrase, the patterns (each with citations), the arc over time, and —
  always — what it *couldn't* determine.

## run it yourself

```bash
git clone https://github.com/kutubalalalaska/peeping-tom && cd peeping-tom
cp .env.example .env       # set ROUTE_A_* (e.g. an OpenRouter key), or ROUTE_A_PROVIDER=mock
docker compose up --build  # app on http://localhost:8000
```

- No GPU required (`DECODE_PROFILE=cpu` tunes decode for CPU boxes; a GPU or
  Apple-Silicon host Ollama makes it much faster).
- Fully-offline flow test: `APP_ENV_FILE=.env.mock docker compose up app --build`
  (no models, no endpoint, mock everything).
- Contract test: `python3 scripts/e2e_mock.py` (`--mode deep` for deep mode).

## license

[AGPL-3.0](./LICENSE). If you run a modified version as a service, you owe your
users the source — for this project that isn't a legal technicality, it's the
point: every privacy promise above is only auditable because the code that
makes it is public.
