"""What the VLM is asked, per kind of event. One place, so the service, the evaluation and the tests use the same words.

The model answers with ONE word. We do not parse free text: the verdict comes from the probabilities of the first token (yes / no / unsure),
which also gives a number to calibrate a threshold on. A reason, when wanted, is a second, short generation that never decides anything.
"""

SYSTEM = ("You are an assistant that checks alerts from a home safety camera. You see a few frames in time order from a fixed camera. "
          "You answer with exactly one word: yes, no or unsure.")

QUESTIONS = {
    "FALL": ("Did a person just FALL to the floor in these frames (lost their balance or collapsed and ended up lying or sitting on the floor "
             "without meaning to)? Lying down on purpose (on a sofa, bed or floor), sitting down, crouching, kneeling, bending to pick something up, "
             "exercising and playing are NOT falls. If nobody is clearly visible, answer no."),
    "FIGHT": ("Are two or more people FIGHTING in these frames (punching, kicking, slapping, shoving or grappling aggressively)? "
              "Hugging, playing, dancing, sports, handshakes and gentle pushing are NOT fights. If fewer than two people are visible, answer no."),
}


def question_for(event_type: str, n_frames: int, seconds_apart: float) -> str:
    q = QUESTIONS.get(event_type)
    if q is None:
        raise KeyError(f"no question for event type {event_type!r}")
    return (f"These are {n_frames} frames, about {seconds_apart:.1f} seconds apart, oldest first. {q} "
            "Answer with one word: yes, no or unsure.")


REASON_SUFFIX = " Then, in one short sentence, say what you see."


# ---- the multiple-choice form (the one used): a yes/no question made a 2B model say "yes, the person is crouching ... not falling" - it answers
# "yes" to the question "is this what I see?". Choosing among descriptions is a task such models are better at, and the letters give clean
# probabilities. The event is confirmed by the probability of the letter that stands for it.
OPTIONS_SYSTEM = ("You are an assistant that checks alerts from a home safety camera. You see a few frames in time order from a fixed camera. "
                  "You answer with exactly one capital letter.")

CHOICES = {
    "FALL": (["A", "falls: loses balance or collapses and ends up on the floor without meaning to"],
             ["B", "lies down or sits down on purpose (on a sofa, a bed or the floor), or exercises"],
             ["C", "crouches, kneels or bends over (for example to pick something up) and is not on the floor"],
             ["D", "stands, walks or sits normally"],
             ["E", "nobody is clearly visible"]),
    "FIGHT": (["A", "fight: punching, kicking, slapping, shoving or grappling aggressively"],
              ["B", "hug, handshake, gentle push or playing"],
              ["C", "sport, dance or exercise together"],
              ["D", "people stand, walk or talk normally"],
              ["E", "fewer than two people are visible"]),
}
LETTERS = "ABCDE"


def options_question(event_type: str, n_frames: int, seconds_apart: float) -> str:
    c = CHOICES.get(event_type)
    if c is None:
        raise KeyError(f"no question for event type {event_type!r}")
    lines = "\n".join(f"{k}. {v}" for k, v in c)
    return (f"These are {n_frames} frames, about {seconds_apart:.1f} seconds apart, oldest first. Which describes best what the person (or people) "
            f"in them does?\n{lines}\nAnswer with one letter.")
