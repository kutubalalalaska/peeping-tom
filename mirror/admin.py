"""The operator's window: GET /admin — a basic-auth'd, self-refreshing status
page (recent requests + outcomes, live jobs, credits, disk) rendered from
GET /api/admin/summary. Enabled only when ADMIN_PASS is set; without it every
admin route answers 404 (the page simply doesn't exist on self-host installs).

Shows ONLY what events.py records + job status minus its rate-moat fields —
no sid/ip, no participant names, no content. server.py injects the live
credits/auth state at import wiring time (CREDITS / ROUTE_AUTH / OUT_OF_CREDITS)
to avoid a circular import.
"""

import base64, secrets, shutil, time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import alerts, events, jobs
from .config import settings

router = APIRouter()
STARTED = time.time()

# Wired by server.py after import (callback pattern, same as uploads.PREPROCESS).
CREDITS = None            # server._CREDITS dict: {"remaining": USD|None, "ts": ...}
ROUTE_AUTH = None         # server._ROUTE_AUTH: {route_id: {"ok": ..., "detail": ...}}
OUT_OF_CREDITS = None     # server._out_of_credits


def _guard(request: Request):
    if not settings.admin_pass:
        raise HTTPException(404)
    h = request.headers.get("authorization") or ""
    if h.lower().startswith("basic "):
        try:
            user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
            if (secrets.compare_digest(user, settings.admin_user)
                    and secrets.compare_digest(pw, settings.admin_pass)):
                return
        except Exception:
            pass
    raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="mirror ops"'})


def _mem():
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            info[k] = int(v.split()[0])            # kB
        return {"available_mb": info["MemAvailable"] // 1024,
                "total_mb": info["MemTotal"] // 1024}
    except Exception:                              # macOS dev box — no /proc
        return None


def _counts(evs):
    c = {"uploads": 0, "done": 0, "failed": 0, "cancelled": 0, "http_5xx": 0, "refused": {}}
    durs = []
    for e in evs:
        ev = e.get("event")
        if ev == "upload_accepted":
            c["uploads"] += 1
        elif ev == "job_done":
            c["done"] += 1
            if e.get("seconds"):
                durs.append(e["seconds"])
        elif ev in ("job_failed", "job_needs_config"):
            c["failed"] += 1
        elif ev == "job_cancelled":
            c["cancelled"] += 1
        elif ev == "http_5xx":
            c["http_5xx"] += 1
        elif ev == "upload_refused":
            r = e.get("reason", "?")
            c["refused"][r] = c["refused"].get(r, 0) + 1
    c["avg_read_s"] = round(sum(durs) / len(durs)) if durs else None
    return c


_JOB_FIELDS = ("state", "phase", "source", "mode", "created_at", "expires_at")


@router.get("/api/admin/summary")
def summary(request: Request):
    _guard(request)
    now = time.time()
    live = []
    for jid in jobs.all_ids():
        s = jobs.get_status(jid) or {}
        j = {"id": jid, **{k: s.get(k) for k in _JOB_FIELDS}}     # no sid/ip — ever
        j["pct"] = (s.get("progress") or {}).get("pct")
        j["age_s"] = round(now - (s.get("created_at") or s.get("ts") or now))
        live.append(j)
    live.sort(key=lambda j: j.get("created_at") or 0, reverse=True)
    week = events.read(since=now - 7 * 86400)
    du = shutil.disk_usage(settings.data_dir)
    return {
        "now": now, "started": STARTED, "hosted": settings.hosted,
        "disk": {"free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1)},
        "mem": _mem(),
        "credits_usd": (CREDITS or {}).get("remaining"),
        "out_of_credits": OUT_OF_CREDITS() if OUT_OF_CREDITS else None,
        "route_auth": ROUTE_AUTH,
        "alerts_enabled": alerts.enabled(),
        "counts": {"24h": _counts([e for e in week if e.get("ts", 0) >= now - 86400]),
                   "7d": _counts(week)},
        "jobs": live[:50],
        "events": list(reversed(events.read(limit=100))),
    }


@router.post("/api/admin/test-alert")
def test_alert(request: Request):
    _guard(request)
    if not alerts.enabled():
        return {"sent": False, "why": "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set"}
    alerts.send("🔔 test alert from the mirror admin page — wiring works")
    return {"sent": True}


PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>peeping tom — ops</title>
<style>
 body{background:#fff;color:#000;font:13px/1.5 "Courier New",monospace;margin:24px;max-width:1100px}
 h1{font-size:15px;margin:0 0 4px} h2{font-size:13px;margin:24px 0 6px;border-bottom:1px solid #000}
 table{border-collapse:collapse;width:100%} td,th{text-align:left;padding:2px 12px 2px 0;vertical-align:top}
 th{border-bottom:1px solid #999;font-weight:normal;color:#555}
 .bad{background:#000;color:#fff;padding:0 4px} .dim{color:#777} .ok{color:#0a0}
 #meta{color:#777} button{font:inherit;background:#fff;border:1px solid #000;cursor:pointer;padding:2px 10px}
 td.detail{word-break:break-word}
</style>
<h1>peeping tom — ops</h1>
<div id="meta">loading…</div>
<div id="health"></div>
<h2>last 24h / 7d</h2><div id="counts"></div>
<h2>jobs on disk</h2><div id="jobs"></div>
<h2>recent events</h2><div id="events"></div>
<p><button onclick="testAlert()">send test alert</button> <span id="alertres"></span></p>
<p class="dim">this page shows job-level technical facts only — no IPs, no names, no content.</p>
<script>
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const ago = s => s==null ? "" : s<90 ? Math.round(s)+"s" : s<5400 ? Math.round(s/60)+"m" : Math.round(s/3600)+"h";
const t = ts => ts ? new Date(ts*1000).toLocaleString(undefined,{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"}) : "";
function row(cells){ return "<tr>"+cells.map(c=>"<td class=detail>"+c+"</td>").join("")+"</tr>"; }
function table(head, rows){ return rows.length ? "<table><tr>"+head.map(h=>"<th>"+h+"</th>").join("")+"</tr>"+rows.join("")+"</table>" : "<span class=dim>none</span>"; }
function counts(c){ const ref = Object.entries(c.refused||{}).map(([k,v])=>k+":"+v).join(" ")||"0";
  return `uploads ${c.uploads} · done ${c.done} · <span class="${c.failed?"bad":""}">failed ${c.failed}</span> · refused ${esc(ref)} · cancelled ${c.cancelled} · 5xx ${c.http_5xx}` + (c.avg_read_s?` · avg read ${ago(c.avg_read_s)}`:""); }
async function refresh(){
  let d; try { d = await (await fetch("/api/admin/summary")).json(); } catch(e){ document.getElementById("meta").innerHTML='<span class=bad>summary fetch failed</span>'; return; }
  document.getElementById("meta").textContent = `app up ${ago(d.now-d.started)} · refreshed ${new Date().toLocaleTimeString()}`;
  const auth = Object.entries(d.route_auth||{}).map(([id,a])=>`route ${id}: ${a.ok===true?'<span class=ok>auth ok</span>':a.ok===false?'<span class=bad>auth FAILED</span>':'unverified'}`).join(" · ");
  document.getElementById("health").innerHTML = [
    d.out_of_credits ? '<span class=bad>OUT OF CREDITS — uploads gated</span>' : null,
    d.credits_usd!=null ? `credits $${d.credits_usd.toFixed(2)}` : "credits n/a",
    `disk ${d.disk.free_gb} / ${d.disk.total_gb} GB free`,
    d.mem ? `mem ${d.mem.available_mb} / ${d.mem.total_mb} MB free` : null,
    auth || null,
    d.alerts_enabled ? '<span class=ok>alerts on</span>' : '<span class=bad>alerts OFF (no telegram env)</span>',
  ].filter(Boolean).join(" · ");
  document.getElementById("counts").innerHTML = "24h &nbsp;"+counts(d.counts["24h"])+"<br>7d &nbsp;&nbsp;"+counts(d.counts["7d"]);
  document.getElementById("jobs").innerHTML = table(["job","state","phase","src","mode","pct","age"],
    d.jobs.map(j=>row([esc(j.id), esc(j.state), esc(j.phase), esc(j.source), esc(j.mode), j.pct??"", ago(j.age_s)])));
  document.getElementById("events").innerHTML = table(["when","event","job","detail"],
    d.events.map(e=>{ const extra = Object.entries(e).filter(([k])=>!["ts","event","job"].includes(k)).map(([k,v])=>k+"="+JSON.stringify(v)).join(" ");
      const cls = /failed|refused|5xx|needs_config|out_of_credits/.test(e.event) ? "bad" : "";
      return row([t(e.ts), `<span class="${cls}">${esc(e.event)}</span>`, esc(e.job||""), esc(extra)]); }));
}
async function testAlert(){ const r = await (await fetch("/api/admin/test-alert",{method:"POST"})).json();
  document.getElementById("alertres").textContent = r.sent ? "sent — check telegram" : (r.why||"failed"); }
refresh(); setInterval(refresh, 5000);
</script>"""


@router.get("/admin", response_class=HTMLResponse)
def page(request: Request):
    _guard(request)
    return PAGE
