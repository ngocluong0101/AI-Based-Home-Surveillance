"""Keeps the VLM service busy while something else is measured (V2-7: M3 "with the VLM working").

    python eval/vlm/load.py --url http://127.0.0.1:18200 --frames DIR_WITH_JPEGS --every 2 --seconds 120 --out load.json

Sends POST /verify (FALL, 6 full-size JPEG frames, with a reason) every --every seconds, one request at a time like the backend's
verifier thread, and writes how long each answer took. Only a load generator: the verdicts are not checked here (eval/vlm/evaluate.py does that).
"""
from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:18200")
    ap.add_argument("--frames", required=True, help="directory with at least 6 JPEGs")
    ap.add_argument("--every", type=float, default=2.0, help="seconds between the starts of two requests")
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--out", help="write the summary here as JSON")
    a = ap.parse_args()
    files = sorted(Path(a.frames).glob("*.jpg"))[:6]
    if len(files) < 6:
        sys.exit("need 6 JPEGs in --frames")
    body = json.dumps({"event_type": "FALL", "seconds_apart": 0.5, "reason": True,
                       "frames": [base64.b64encode(f.read_bytes()).decode() for f in files]}).encode()
    lat: list[float] = []
    errors = 0
    t_end = time.monotonic() + a.seconds
    nxt = time.monotonic()
    while time.monotonic() < t_end:
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(a.url + "/verify", body, {"content-type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
            lat.append(time.monotonic() - t0)
        except Exception as exc:                      # a failing VLM is a result, not a crash
            errors += 1
            print("error:", type(exc).__name__, file=sys.stderr)
        nxt += a.every
        time.sleep(max(0.0, nxt - time.monotonic()))
    out = {"requests": len(lat), "errors": errors, "every_s": a.every,
           "latency_median_s": round(statistics.median(lat), 2) if lat else None,
           "latency_max_s": round(max(lat), 2) if lat else None}
    print(json.dumps(out))
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1))
    return 0 if lat else 1


if __name__ == "__main__":
    sys.exit(main())
