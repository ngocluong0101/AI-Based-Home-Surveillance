"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

V2-3: look inside the fall rule on a clip. Reads the dump of `ds_engine --dump-detections` made with the pose model, feeds the
skeletons to the Python specification (src/events/behavior.py) and prints, for every Nth frame, the torso angle, the hips and the
state of the fall rule, plus the events.

    eval/tools/in_container.sh engine/build/ds_engine --config /work/eval/work/pgie_pose_b1.txt --tracker-config /work/engine/configs/tracker_nvdcf.yml \
        --camera 1=file:///work/eval/work/urfall/fall-01.mp4 --dump-detections /work/eval/work/urfall/fall-01.csv --fps-interval 0
    eval/.venv/bin/python eval/behavior/trace_fall.py eval/work/urfall/fall-01.csv --every 5

The dump is in the 1920x1080 frame the engine works in, at the camera's frame rate.
"""
import argparse
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from eval.common import ds_dump  # noqa: E402
from src.events import BehaviorConfig, BehaviorEngine, Person  # noqa: E402
from src.events.behavior import _torso  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", type=Path)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="BehaviorConfig field, e.g. fall_still_s=1.0")
    args = ap.parse_args()

    cfg = BehaviorConfig(fall=True)
    for item in args.set:
        k, v = item.split("=")
        setattr(cfg, k, type(getattr(cfg, k))(float(v)) if not isinstance(getattr(cfg, k), bool) else v == "true")
    d = ds_dump.load_dump(args.dump)
    trk = d["trk"].dropna(subset=["kpts"])
    engine = BehaviorEngine(cfg, [], 1920, 1080)
    n = len(d["frames"])
    print(f"{n} frames; fall_down_deg={cfg.fall_down_deg} fall_up_deg={cfg.fall_up_deg} drop_window={cfg.fall_drop_window_s}s "
          f"hip_drop_min={cfg.fall_hip_drop_min} still={cfg.fall_still_s}s")
    print("   t(s)  id  angle  torso  hip(x,y)        state")
    for frame in range(n):
        now = int(round(frame / args.fps * 1e9))
        rows = trk[trk.frame_num == frame]
        people = []
        for _, r in rows.iterrows():
            v = [float(x) for x in r.kpts.split()]
            people.append(Person(int(r.object_id), (r.left, r.top, r.width, r.height), [tuple(v[3 * k:3 * k + 3]) for k in range(17)]))
        events = engine.process(people, now)
        if frame % args.every == 0 or events:
            for p in people:
                m = _torso(p.keypoints, cfg.vis_min)
                st = engine.tracks[p.track_id].fall.mode if p.track_id in engine.tracks else "-"
                if m:
                    print(f"{frame / args.fps:7.2f} {p.track_id:3d} {m[0]:6.1f} {m[1]:6.0f}  ({m[3][0]:5.0f},{m[3][1]:5.0f})  {st}")
                else:
                    print(f"{frame / args.fps:7.2f} {p.track_id:3d}    n/a    n/a  (torso not visible)  {st}")
        for e in events:
            print(f"   >>> {e.type} track {e.track_ids} began {e.occurred_ns / 1e9:.2f}s reported {e.now_ns / 1e9:.2f}s score {e.score:.2f}")


if __name__ == "__main__":
    main()
