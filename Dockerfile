# --- stage 1: build the React/TS frontend ---
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build          # -> /web/dist

# --- stage 2: the python app, serving the built SPA ---
FROM python:3.11-slim
# apt over Docker Desktop's VM network truncates downloads at random ("Hash Sum
# mismatch" / "File has unexpected size"). Force IPv4 + no pipelining, then retry
# the whole install: good .debs stay cached, only the corrupted one is re-fetched,
# so it converges in a couple of passes.
RUN printf 'Acquire::ForceIPv4 "true";\nAcquire::http::Pipeline-Depth "0";\nAcquire::Retries "5";\n' \
      > /etc/apt/apt.conf.d/99robust \
    && for i in 1 2 3 4 5 6; do \
         apt-get update \
           && apt-get install -y --no-install-recommends --fix-missing ffmpeg curl libgl1 libglib2.0-0 \
           && rm -rf /var/lib/apt/lists/* && exit 0; \
         echo ">>> apt attempt $i failed (truncated download); retrying…"; sleep 5; \
       done; exit 1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Pre-bake the NudeNet model into the image so the explicit gate never does a flaky
# runtime download (and so a broken detector surfaces at build, not mid-read).
RUN python -c "from nudenet import NudeDetector; NudeDetector(); print('nudenet ready')"

COPY mirror ./mirror
COPY scripts ./scripts
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh
COPY --from=web /web/dist ./web

ENV DATA_DIR=/data
ENV WEB_DIR=/app/web
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
