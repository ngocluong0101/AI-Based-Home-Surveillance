# Why my detector found 10% fewer people than Ultralytics (and why it was not a bug)

I ported a YOLOv8n person detector from Ultralytics to a DeepStream/TensorRT pipeline. On the same video, the DeepStream
version reported about 11% fewer people. For a safety system that is the kind of number that keeps you awake: is the
port broken?

It was not. Taking the 11% apart is the part worth writing down, because the answer was not "FP16" and not "a bug".

## The method: change one thing at a time

I replayed three fixed-camera 1080p clips (MOT17, 2175 frames) through four things and counted boxes at confidence 0.25:

1. Ultralytics with its defaults.
2. A Python replica of my pipeline (ONNX Runtime FP32), checked against Ultralytics first: 32,495 boxes against
   32,487 at NMS 0.7 and 31,316 against 31,312 at NMS 0.45. If your replica does not reproduce the reference, nothing
   after it means anything.
3. The same replica with one setting at a time moved towards what the engine does.
4. The real engine.

| Step | Boxes | Gap to the engine | What it explains |
|---|---|---|---|
| Ultralytics defaults (NMS IoU 0.7, rectangular letterbox, grey padding 114) | 32,487 | +11.2% | |
| NMS IoU 0.45 (the engine's setting) | 31,312 | +7.2% | **4.0 points** |
| square 640x640 input with black padding | 29,921 | +2.4% | **4.8 points** |
| nearest-neighbour resize (nvinfer's default) | 29,657 | +1.5% | 0.9 points |
| FP16 TensorRT | 29,214 | 0 | ~1.5 points |

Nearly nine of the eleven points are two configuration choices, not numerical error.

## What the two big ones are

**NMS threshold.** Ultralytics defaults to an IoU of 0.7 for non-maximum suppression; I had set 0.45. A looser threshold keeps
more overlapping boxes. More boxes is not better: with the Ultralytics defaults recall was 0.379 against 0.359 for the
engine, but precision was 0.833 against 0.879. The extra boxes were mostly duplicates.

**Letterboxing.** Ultralytics resizes a 1920x1080 frame to 640x360 and pads to 640x384 with grey (value 114). My engine
padded the 16:9 frame into a *square* 640x640 with black. That puts 140 pixels of black above and below the picture, which
YOLOv8 never saw in training (it trains with grey padding). That cost 4.8 points. The cause is my reasonable guess; the
4.8 is measured, the mechanism I did not isolate.

The rest of the engine-versus-replica difference (the last ~1.5 points) was almost all boxes sitting right at the
threshold: about 77% of the boxes that existed on only one side had a confidence under 0.35. That is arithmetic noise
moving boxes across a line, not a missing feature.

## What I changed, and what I did not

Feeding the engine a rectangular 640x384 input (same ONNX file, a new TensorRT engine) gave 3.4% more detections and a
slightly better tracker, with 40% fewer anchors to compute. I did not make it the default: it needs the model input shaped
like the camera's aspect ratio, and the engine manager has to choose it per camera. It is written down as a known gain,
not shipped as a claim.

I also measured that the TensorRT FP16 detector is within 1 AP point of a FP32 replica on the same pixels (0.457 against
0.464 on 1000 COCO images). FP16 was not the problem.

## What I would tell someone else

- Reproduce the reference with your own replica *first*. Mine matched to 4 boxes in 32,000, which is what made the later
  steps believable.
- Compare on the **same decoded pixels**. I encoded every test image to H.264 once and fed that file to every pipeline.
  Otherwise your gap includes decoder differences you did not mean to measure.
- A gap between two pipelines is rarely one cause. Write the table, one row per change.
- "Fewer detections" is not "worse". Check precision before you chase recall.

One caution on scope: these clips are crowded street scenes with small people. Indoors the proportions may differ, which is
why the real indoor footage is still on my to-do list.

Numbers: `eval/results/2026-11-19_v2-1_pipeline_gap.json`, `..._coco_ap.json`. Re-run: `eval/README.md`.
