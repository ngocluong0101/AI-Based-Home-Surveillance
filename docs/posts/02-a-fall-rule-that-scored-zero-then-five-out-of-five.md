# A fall rule that scored 0 out of 5, then 5 out of 5, and why the second number meant little

I wrote a rule for detecting a fall from a body skeleton: the torso tips past 60 degrees, the hips drop, the person stays on
the floor for a couple of seconds. I wrote unit tests, it passed them, and I was pleased with it.

Then I ran it on five real clips from a public fall dataset (UR Fall). It caught **none of them**.

## Why it failed

The skeleton detector was fine. The rule was wrong in two ways I could not have found by thinking:

1. I checked "the hips have dropped" at the instant the torso passed 60 degrees. But in a real fall the hips **keep
   falling for another 0.2 to 0.5 seconds** after that.
2. I took "lying still" to start at the moment of impact. A real person slides or rolls a little, and my rule read that as
   movement and reset.

I fixed both. The fix introduced a regression: slowly lying down on a sofa now looked like a fall. My hand-written test
caught it; the tests generated from the engine's own output did not. That matters:

> **Test vectors generated from your own implementation prove that two implementations agree. They do not prove the rule is
> right.** My C port and my Python reference agree on 25 generated vectors. Only the hand-written tests and the real clips
> said anything about falls.

So I added a speed criterion (hips must fall at least half a torso length within 0.8 s: real falls took 0.2 to 0.3 s,
lying down on purpose took over a second) and kept the hand-written tests as the judge.

## Then 5 out of 5, and why I did not believe it

After the fixes the rule caught 5 of 5 clips with no false alarms on 3 daily-activity clips. I did not put that in the
README, because I had tuned the rule while looking at exactly those clips. A score on the data you tuned on is a debugging
signal, not a measurement.

So I built the evaluation properly: all 70 sequences of the dataset (30 falls, 40 daily activities), 5-fold
cross-validation by sequence, skeletons taken from the deployed engine rather than the dataset's own annotations, and the
rule scored exactly like the network. The honest figure for the tuned rule:

| | Falls caught (of 30) | False alarms (of 40 activity sequences) |
|---|---|---|
| rule, default settings | 2 | 1 |
| rule, tuned on 5 clips | 14 | **6** |
| ST-GCN skeleton network (T=48, threshold 0.9) | **27.3** | **0.7** |

The "5 of 5" rule produced false alarms in 6 of the 40 daily-activity sequences, people lying down on the floor on purpose, and
it caught fewer than half of the 30 falls. That is the number I would have shipped.

**These are not the first numbers I got.** I first measured 20 of 30 and 11 of 40 for the tuned rule, and 29.3 and 0.7 for
the network. Then I found a geometry bug: UR Fall is 4:3 and my pipeline stretched every non-16:9 picture to fill a 16:9
frame, so people were 1.33 times too wide and every body angle was tilted. I fixed it, re-ran the skeleton extraction, the
cross-validation and the replay of the rules, and the table above is the corrected one. The rule lost a lot (its thresholds had
been tuned on stretched skeletons), the network lost a little, and the conclusion did not change. The lesson I took: a number
you have published is a claim about your pipeline *as it was*; when you fix the pipeline, measure again and say that you did.

## What the network is, and what it does not show

The network is a small ST-GCN (about 98,000 parameters, 405 kB of ONNX): graph convolutions over the skeleton's joints and a
temporal convolution over 48 frames, run through TensorRT in a worker thread so the video thread never waits on it. It
beat a plain MLP and a temporal CNN at the same threshold, and removing the velocity input roughly doubled its false alarms.
That is some evidence that the graph structure and the motion both matter.

What it does not show:

- **Everything is one room.** UR Fall has one camera view, one background, volunteers acting falls.
- **The threshold was picked by looking at these same results** (0.9 is the engine's default; 0.95 gave fewer false alarms and fewer falls caught). Optimistic by construction.
- **There are no real fights in the data**, so there is no fight model, only a rule.

I still consider the network the right default over the rule, because it is not tuned by hand on a handful of clips. But I
have not shown it works in a stranger's living room, and the repository says so on its front page.

## What I would tell someone else

- Write the rule, then run it on real data **before** you write the README.
- Keep the tests that check behaviour separate from the tests that check agreement.
- When you tune on the data, report it as tuned. Better, evaluate on sequences you never looked at.
- A comfortable small-sample number is the one to be suspicious of.

Numbers: `eval/results/2026-11-19_v2-5_cv_urfall.json`. Code: `src/events/behavior.py` (the specification),
`engine/src/core/behavior.c`, `eval/train/`.
