"""Stands in for ds_engine in the manager tests.

Records how it was started in $FAKE_ENGINE_DIR/run-<n>.json (argv, whether DS_ENGINE_TOKEN was set, and
the cameras file with its mode), prints a line that has a password in it, then behaves as FAKE_MODE says:
  run (default)  wait for SIGTERM, print "Stopping", exit 0
  crash          exit 3 after a moment
  finish         exit 0 at once (every source ended)
  stubborn       ignore SIGTERM
In every mode but stubborn it also takes SIGHUP like the real engine (a reload of the settings): it copies the cameras
file as it is at that moment to $FAKE_ENGINE_DIR/hup-<n>.json (n counts the signals), so a test can see that the file
was already rewritten when the signal arrived. It then prints what the real engine prints: "reload: done, ..." or, when
FAKE_RELOAD_FAIL is set, "reload: nothing applied, ..." (the engine could not do what the file asks).
"""
import json
import os
import signal
import sys
import time

rec_dir = os.environ["FAKE_ENGINE_DIR"]
mode = os.environ.get("FAKE_MODE", "run")

args = sys.argv[1:]
cams_path = args[args.index("--cameras-file") + 1]


def on_hup(*_):
    k = len([f for f in os.listdir(rec_dir) if f.startswith("hup-")]) + 1
    with open(cams_path, encoding="utf-8") as src:
        text = src.read()
    tmp = os.path.join(rec_dir, f"hup-tmp-{k}")
    with open(tmp, "w") as dst:
        json.dump({"cameras": text}, dst)
    os.replace(tmp, os.path.join(rec_dir, f"hup-{k}.json"))
    if os.environ.get("FAKE_RELOAD_FAIL"):
        print("reload: nothing applied, there is no free place for the new cameras (see --max-cameras) "
              "(the engine has to be restarted for that)", flush=True)
    else:
        print("reload: done, 0 added, 0 removed, 0 with new settings", flush=True)


signal.signal(signal.SIGHUP, on_hup)   # before anything is recorded: the manager may signal at once
n = len([f for f in os.listdir(rec_dir) if f.startswith("run-")]) + 1
with open(cams_path, encoding="utf-8") as f:
    cams = f.read()
tmp = os.path.join(rec_dir, f"tmp-{n}")
with open(tmp, "w") as f:
    json.dump({"argv": sys.argv, "token": os.environ.get("DS_ENGINE_TOKEN"),
               "cameras": cams, "mode_bits": oct(os.stat(cams_path).st_mode & 0o777)}, f)
os.replace(tmp, os.path.join(rec_dir, f"run-{n}.json"))   # appears whole or not at all

print("source_id=0 uri=rtsp://admin:hunter2@10.0.0.5/stream", flush=True)

if mode == "finish":
    sys.exit(0)
if mode == "crash":
    time.sleep(0.2)
    sys.exit(3)
if mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(0.1)

stop = []
signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
while not stop:
    time.sleep(0.05)
print("Stopping", flush=True)
sys.exit(0)
