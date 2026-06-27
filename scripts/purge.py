#!/usr/bin/env python3
"""Self-destruct sweep for a real system cron (the server also runs this in-process
on a timer, so this is only needed if you prefer an external cron).

Deletes, per mirror/jobs.purge_expired():
  - FINISHED reads past their TTL (READ_TTL_SECONDS after the read was ready), and
  - UNFINISHED/abandoned jobs older than MAX_JOB_AGE_SECONDS (the garbage rule).

Run it with the SAME DATA_DIR the server uses, e.g. inside the container:

    docker compose -f docker-compose.prod.yml exec app python scripts/purge.py

or from a host cron (adjust DATA_DIR to the mounted volume path):

    */5 * * * * cd /srv/mirror-app && DATA_DIR=/var/lib/mirror python3 scripts/purge.py >> /var/log/mirror-purge.log 2>&1
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mirror import jobs                         # noqa: E402  (after sys.path tweak)
from mirror.config import settings              # noqa: E402

if __name__ == "__main__":
    n = jobs.purge_expired()
    print(f"[purge] data_dir={settings.data_dir} deleted={n}")
