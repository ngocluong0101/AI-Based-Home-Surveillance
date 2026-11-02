#!/usr/bin/env python3
"""Fake backend for testing the engine's publisher (ROADMAP 2.6).

Accepts POST /internal/events like the real backend will, appends the body of every request it
answers with 2xx to --log (one JSON event per line), and can misbehave on purpose:

  --delay SEC         wait SEC before answering (a slow backend)
  --status CODE       answer with this HTTP status instead of 200 ...
  --status-for SEC    ... but only for the first SEC seconds after start, then answer 200
  --token TOKEN       answer 401 unless the request carries the header X-Internal-Token: TOKEN

Not part of the product: an offline test tool.
"""
import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--status", type=int, default=200)
    ap.add_argument("--status-for", type=float, default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--log", required=True, help="file that receives the accepted events")
    args = ap.parse_args()

    started = time.monotonic()
    lock = threading.Lock()
    counts = {"requests": 0, "accepted": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 (name fixed by BaseHTTPRequestHandler)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if args.delay:
                time.sleep(args.delay)
            failing = args.status != 200 and (
                args.status_for is None or time.monotonic() - started < args.status_for
            )
            status = args.status if failing else 200
            if args.token is not None and self.headers.get("X-Internal-Token") != args.token:
                status = 401
            if self.path != "/internal/events":
                status = 404
            if status == 200:
                try:
                    json.loads(body)
                except ValueError:
                    status = 400
            with lock:
                counts["requests"] += 1
                if status == 200:
                    counts["accepted"] += 1
                    with open(args.log, "ab") as f:
                        f.write(body.rstrip(b"\n") + b"\n")
            self.send_response(status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, fmt, *a):  # one line per request on stderr
            sys.stderr.write("%.1fs %s\n" % (time.monotonic() - started, fmt % a))

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print("requests=%d accepted=%d" % (counts["requests"], counts["accepted"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
