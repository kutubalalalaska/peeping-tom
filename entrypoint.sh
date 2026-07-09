#!/usr/bin/env bash
# Wait for the bundled Ollama and ensure the vision model is present, then serve.
# Skipped entirely when VISION_BACKEND=mock (quick flow test, no Ollama needed).
set -e

if [ "${VISION_BACKEND:-ollama}" = "mock" ]; then
  echo "VISION_BACKEND=mock — skipping Ollama."
else
  OLLAMA="${OLLAMA_HOST:-http://ollama:11434}"
  # The effective vision model follows DECODE_PROFILE (mirror/config.py) unless
  # VISION_MODEL explicitly overrides it — mirror the same fallback here.
  if [ -n "${VISION_MODEL:-}" ]; then
    VMODEL="$VISION_MODEL"
  elif [ "${DECODE_PROFILE:-gpu}" = "cpu" ]; then
    VMODEL="qwen2.5vl:3b"
  else
    VMODEL="qwen2.5vl:7b"
  fi
  echo "waiting for Ollama at $OLLAMA …"
  until curl -sf "$OLLAMA/api/tags" >/dev/null 2>&1; do sleep 2; done
  echo "ensuring local vision model: $VMODEL (first run downloads it)…"
  curl -s "$OLLAMA/api/pull" -d "{\"name\":\"$VMODEL\"}" >/dev/null || \
    echo "  (could not pre-pull $VMODEL; decode will pull on first use)"
fi

echo "starting Drop 001: Peeping Tom on :8000"
exec uvicorn mirror.server:app --host 0.0.0.0 --port 8000
