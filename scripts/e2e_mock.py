"""End-to-end flow test against a running app (mock or real stack).

Builds a tiny synthetic WhatsApp export (text + image + sticker + voice + video),
uploads it, polls the job to completion, then verifies the result contract:
read text present, citations resolve via /messages, transcript + retained + delete
all answer. Run it after any pipeline change:

    docker compose -f docker-compose.mock.yml up --build -d
    python3 scripts/e2e_mock.py                      # default http://localhost:8000
    python3 scripts/e2e_mock.py --mode deep          # once modes exist
    python3 scripts/e2e_mock.py --keep               # don't delete the job at the end

Exit code 0 = every check passed. Stdlib only — no deps, runs anywhere.
"""

import argparse, io, json, sys, time, urllib.error, urllib.request, uuid, zipfile

CHAT = """\
[12.03.2024, 10:15:22] Anna: good morning. did you sleep at all?
[12.03.2024, 10:16:03] Marco: barely. the deadline thing again
[12.03.2024, 10:16:40] Anna: you always say "the thing" when you don't want to talk about it
[12.03.2024, 10:18:11] Marco: ‎<attached: 00000005-PHOTO-2024-03-12-10-18-11.jpg>
[12.03.2024, 10:19:02] Anna: is that the whiteboard? you could just tell me
[12.03.2024, 11:02:45] Marco: ‎<attached: 00000007-AUDIO-2024-03-12-11-02-45.opus>
[12.03.2024, 11:04:10] Anna: ok. that actually explains a lot. thank you
[13.03.2024, 09:30:00] Marco: ‎<attached: 00000009-STICKER-2024-03-13-09-30-00.webp>
[13.03.2024, 09:31:12] Anna: you and your stickers instead of words
[14.03.2024, 20:11:05] Marco: ‎<attached: 00000011-VIDEO-2024-03-14-20-11-05.mp4>
[14.03.2024, 20:15:33] Anna: you looked tired in that. come home earlier tomorrow
[15.03.2024, 08:00:19] Marco: I will. promise
[15.03.2024, 08:01:00] Anna: you said that on tuesday too ‎<attached: 00000014-PHOTO-2024-03-15-08-01-00.jpg>
[15.03.2024, 08:05:47] Marco: this time I mean it
"""

MEDIA = [
    "00000005-PHOTO-2024-03-12-10-18-11.jpg",
    "00000007-AUDIO-2024-03-12-11-02-45.opus",
    "00000009-STICKER-2024-03-13-09-30-00.webp",
    "00000011-VIDEO-2024-03-14-20-11-05.mp4",
    "00000014-PHOTO-2024-03-15-08-01-00.jpg",
]


def build_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("_chat.txt", CHAT)
        for name in MEDIA:
            z.writestr(name, b"\x00" * 64)      # mock decode never reads the bytes
    return buf.getvalue()


def api(base, path, data=None, method=None, ctype=None):
    req = urllib.request.Request(base + path, data=data, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        return json.loads(body) if body.strip().startswith((b"{", b"[")) else body.decode()


def upload(base, blob, source="whatsapp", lang="en", mode=None):
    boundary = uuid.uuid4().hex
    parts = []
    fields = {"source": source, "lang": lang}
    if mode:
        fields["mode"] = mode
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    parts.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                  f"filename=\"export.zip\"\r\nContent-Type: application/zip\r\n\r\n").encode())
    parts.append(blob)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return api(base, "/api/upload", b"".join(parts), ctype=f"multipart/form-data; boundary={boundary}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--mode", default=None, help="fast|deep (once modes exist)")
    ap.add_argument("--keep", action="store_true", help="don't delete the job at the end")
    ap.add_argument("--timeout", type=int, default=300, help="seconds to wait for done")
    a = ap.parse_args()

    fails = []

    def check(name, ok, detail=""):
        print(f"  {'✓' if ok else '✗ FAIL'} {name}" + (f" — {detail}" if detail else ""))
        if not ok:
            fails.append(name)

    print(f"[e2e] upload → {a.base}" + (f" (mode={a.mode})" if a.mode else ""))
    jid = upload(a.base, build_zip(), mode=a.mode)["job_id"]
    print(f"[e2e] job {jid}")

    seen, status, t0 = [], {}, time.time()
    while time.time() - t0 < a.timeout:
        status = api(a.base, f"/api/jobs/{jid}")
        key = (status.get("state"), status.get("phase"), status.get("message"))
        if not seen or key != seen[-1]:
            seen.append(key)
            ph = f" phase={key[1]}" if key[1] else ""
            print(f"  [{time.time()-t0:5.1f}s] {key[0]}{ph}: {key[2]}")
        if status.get("state") in ("done", "error", "needs_config"):
            break
        time.sleep(0.5)

    check("reaches done", status.get("state") == "done", f"final state={status.get('state')}")
    if status.get("state") != "done":
        print(json.dumps(status, indent=2, ensure_ascii=False)[:2000]); sys.exit(1)

    res = api(a.base, f"/api/jobs/{jid}/result")
    read = res.get("read") or ""
    cites = res.get("citations") or []
    check("read text present", len(read) > 100, f"{len(read)} chars")
    check("citations present", len(cites) > 0, f"{cites}")

    msgs = api(a.base, f"/api/jobs/{jid}/messages?ids=" + ",".join(map(str, cites))) if cites else []
    got = {m["id"] for m in msgs}
    unresolved = [c for c in cites if c not in got]
    check("all citations resolve", not unresolved, f"unresolved={unresolved}" if unresolved else f"{len(got)} resolved")

    # The mock read always includes an invented [#99999] — server-side validation
    # must strip it from the text and count it.
    if res.get("route") == "mock":
        check("invented id stripped from text", "#99999" not in read)
        check("citations_dropped counted", (res.get("citations_dropped") or 0) >= 1,
              f"dropped={res.get('citations_dropped')}")

    allm = api(a.base, f"/api/jobs/{jid}/messages")
    check("full messages list (drawer)", isinstance(allm, list) and len(allm) >= 14, f"{len(allm)} messages")

    tr = api(a.base, f"/api/jobs/{jid}/transcript")
    check("transcript view answers", "#0" in tr or "#1" in tr)
    # media placeholders actually made it into the crossing text
    for probe in ("voice message", "sticker", "video", "image"):
        check(f"transcript carries [{probe}…]", probe in tr)

    ret = api(a.base, f"/api/jobs/{jid}/retained")
    check("retained readout", isinstance(ret, dict) and "read" in ret, json.dumps(ret))

    if res.get("mode") or a.mode:
        check("result carries mode", res.get("mode") == (a.mode or "fast"), f"mode={res.get('mode')}")

    if not a.keep:
        d = api(a.base, f"/api/jobs/{jid}", method="DELETE")
        check("delete (nuke) answers", d.get("deleted") is True)
        try:
            api(a.base, f"/api/jobs/{jid}")
            check("job gone after delete", False, "status still answers")
        except urllib.error.HTTPError as e:
            check("job gone after delete", e.code == 404, f"HTTP {e.code}")

    print(f"[e2e] {'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}"
          f"  ({time.time()-t0:.1f}s, {len(seen)} status transitions)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
