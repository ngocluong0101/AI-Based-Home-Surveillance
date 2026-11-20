# A language model that said "yes" to everyone, and a GPU that ran out of memory

Two stories from adding a vision-language model (VLM) to a fall detector as a second opinion. Both are about what a safety
system should do when a part of it is wrong, slow, or gone.

## The idea

The skeleton rules and network raise an alert. Before it reaches a phone, a local VLM (Qwen3-VL-2B) looks at six frames
around the event and says whether it really was a fall. The goal is fewer false alarms: someone sitting down on the floor
should not wake you up.

## Story 1: the question decides the answer

My first prompt was the obvious one: "Did this person fall? Answer yes or no."

The numbers looked good. Ranked by the probability of "yes", the model separated falls from non-falls very well (AUC 0.991).
But the *absolute* answers were useless: for a person squatting, the model would reply "Yes, the person is crouching ... not
falling". It was answering "yes, that is a correct description of what I see" to the question as a whole. At every threshold
I tried, it removed at most 3 of the 11 false alarms the rules had at that point.

I changed the question to a multiple choice:

> A. falls: loses balance or collapses and ends up on the floor without meaning to
> B. lies or sits down on purpose ...  C. crouches or bends ...  D. ...  E. ...

and read the probability of the first token "A". The ranking quality barely changed (AUC 0.996) but now the probability
meant something. With the rule that "no" is only acted on when P(A) is below 0.1:

- the rules' 6 false alarms: **2 removed** (4 with a looser threshold, P(A) below 0.5), and **0 of 14 real falls lost**;
- the network raised 2 false alarms; the VLM removed neither, and lost 0 of its 25 true detections.

(Thresholds were read from the same data, so those are optimistic, the dataset is one room, and a handful of false alarms is
a small sample: "2 of 6" could easily be 1 or 4 with other clips. These are the numbers after I fixed a picture-stretching bug;
before the fix the rules had 11 false alarms and caught more falls, see the previous article.)

The lesson I keep: **a metric that ranks well (AUC) can hide an answer format that is unusable.** Check what the model says
at the threshold you will actually use.

## Story 2: it has to see what happens after

The network raises its alert *during* the fall, about half a second before the person is lying down. If the VLM only gets
frames up to the alert, it sees someone mid-air and says no. With frames up to 0.5 s after the alert and a threshold of 0.5
it **rejected 10 of 25 real falls** (3 to 10 over thresholds up to 0.5). Giving it frames up to 2 s after the alert brought that
to 1 of 25 (and 0 at the threshold I ship).

The price is time: the alert waits 2.5 s for those frames, then 1.9 s for the model. About 4.5 s from decision to Telegram,
against 0.3 s without the check. The web app and the WebSocket see the event immediately as "pending"; only the phone
notification waits.

## Fail-open

The VLM is never allowed to swallow a fall. If it is down, slow (more than 8 s), unsure, or has no frames, the alert goes out
unchanged, marked "not checked by the VLM". I tested the real chain in Docker: with the VLM running, a fall came back
"confirmed" (P = 0.994); I stopped the VLM container and replayed the same fall: it was stored and alerted, as
`unverified`, reason "VLM error (ConnectError)". Only a confident "this was not a fall" silences an alert, and even then the
event is stored and visible.

## Story 3: the GPU that ran out of memory

On an 8 GB laptop GPU, 12 cameras, the video encoder and the VLM together left about 100 MB free. The engine crashed in
about one run in five, and when I looked closer in eight repeated runs, seven of the eight began with `Cuda failure: status=2`
(allocation failed) and the first with a CUDA illegal-address error; after that came more illegal-address errors and sometimes a
plain segfault. It does not
fail cleanly when memory runs out; the allocation error just spreads.

Things I did:

- measured the real memory cost per camera place and fitted a small formula (~0.4 GiB for 1 place, ~1.5 GiB for 8, ~2.6 GiB
  for 12 with video);
- made the engine read the free GPU memory at start and **refuse to start** (exit code 4, a clear message, the supervisor
  retries later) if the estimate does not fit, instead of dying minutes later;
- made it print a stack trace on SIGSEGV, because a debugger and core dumps are not available in a container (gdb was no
  use here: it takes SIGINT for itself, which my test harness used to stop the engine).

An honest limit: the estimate refuses 16 places with the VLM running but lets the borderline 12 through, because 12 fits on
paper by a small margin. The practical rule I wrote down is 8 cameras with the VLM on an 8 GB card.

## What I would tell someone else

- Test your safety net by taking it away (stop the VLM and see that alerts still go out).
- Read the answer a model gives at the threshold you will use, not only its ranking score.
- Resource limits are failure modes too: decide in advance what the system does when memory runs out, because the library
  will not decide it for you.

Numbers: `eval/results/2026-11-19_v2-6_vlm_urfall.json`, `eval/results/2026-11-20_v2-7_benchmark_*`. Code: `vlm_service/`,
`backend/verifier.py`, `engine/src/core/vram.c`.
