"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

The VLM that checks an alert (V2-6): Qwen3-VL-2B-Instruct (Apache 2.0) on the GPU in half precision (~4.3 GB), loaded once.

verify() does ONE forward pass over the frames and the question and reads the probabilities of the first answer token (no
decoding). The verdict is `yes` when p_yes >= yes_thr, `no` when p_no >= no_thr, else `unsure`: two thresholds because "no" is
the dangerous answer (it silences a real fall). torch / transformers are imported inside the class.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .prompts import LETTERS, OPTIONS_SYSTEM, REASON_SUFFIX, SYSTEM, options_question, question_for

DEFAULT_MODEL = "Qwen/Qwen3-VL-2B-Instruct"


@dataclass
class Verdict:
    verdict: str                      # yes | no | unsure
    p_yes: float
    p_no: float
    p_unsure: float
    latency_s: float
    reason: str = ""
    frames: int = 0
    extra: dict = field(default_factory=dict)


def decide(p_yes: float, p_no: float, yes_thr: float, no_thr: float) -> str:
    """yes when sure it is the event, no only when sure it is not, otherwise unsure. (An alert marked unsure still goes out.)
    p_no is the probability that it is NOT the event: with the multiple-choice question it is 1 - p_yes."""
    if p_yes >= yes_thr:
        return "yes"
    if p_no >= no_thr:
        return "no"
    return "unsure"


class QwenVL:
    def __init__(self, model_id: str = DEFAULT_MODEL, device: str = "cuda", dtype: str = "bfloat16", max_pixels: int = 640 * 480,
                 yes_thr: float = 0.5, no_thr: float = 0.9, style: str = "options"):
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        self.torch = torch
        self.device = device
        self.yes_thr, self.no_thr = yes_thr, no_thr
        self.style = style      # "options" (letters A-E, the default) or "yn" (one word: yes / no / unsure)
        self.processor = AutoProcessor.from_pretrained(model_id, min_pixels=64 * 28 * 28, max_pixels=max_pixels)
        self.model = AutoModelForImageTextToText.from_pretrained(model_id, dtype=getattr(torch, dtype)).to(device).eval()
        tok = self.processor.tokenizer
        # the first token of each answer word, with and without a capital letter and a leading space
        def ids(words):
            out = set()
            for w in words:
                for form in (w, w.capitalize(), " " + w, " " + w.capitalize()):
                    t = tok.encode(form, add_special_tokens=False)
                    if t:
                        out.add(t[0])
            return sorted(out)
        self.ids = {"yes": ids(["yes"]), "no": ids(["no"]), "unsure": ids(["unsure"])}
        self.letter_ids = [sorted({tok.encode(f, add_special_tokens=False)[0] for f in (c, " " + c)}) for c in LETTERS]

    def _inputs(self, event_type: str, frames, seconds_apart: float, extra_text: str = ""):
        content = [{"type": "image", "image": f} for f in frames]
        ask = options_question if self.style == "options" else question_for
        content.append({"type": "text", "text": ask(event_type, len(frames), seconds_apart) + extra_text})
        messages = [{"role": "system", "content": [{"type": "text", "text": OPTIONS_SYSTEM if self.style == "options" else SYSTEM}]},
                    {"role": "user", "content": content}]
        return self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt").to(self.device)

    def verify(self, event_type: str, frames, seconds_apart: float = 0.5, reason: bool = False) -> Verdict:
        torch = self.torch
        t0 = time.perf_counter()
        with torch.inference_mode():
            inputs = self._inputs(event_type, frames, seconds_apart)
            logits = self.model(**inputs).logits[0, -1].float()
            p = torch.softmax(logits, dim=-1)
            if self.style == "options":
                mass = [float(p[v].sum()) for v in self.letter_ids]
                total = sum(mass) or 1.0
                py = mass[0] / total                   # A: the event itself
                pn, pu = 1.0 - py, mass[4] / total     # not the event; "nobody visible" is its own letter
                extra = {"letters": [round(m / total, 4) for m in mass]}
            else:
                mass = {k: float(p[v].sum()) for k, v in self.ids.items()}
                total = sum(mass.values()) or 1.0
                py, pn, pu = (mass[k] / total for k in ("yes", "no", "unsure"))
                extra = {}
            text = ""
            if reason:
                inputs = self._inputs(event_type, frames, seconds_apart, REASON_SUFFIX)
                out = self.model.generate(**inputs, max_new_tokens=48, do_sample=False)
                text = self.processor.batch_decode(out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip()
        return Verdict(decide(py, pn, self.yes_thr, self.no_thr), py, pn, pu, time.perf_counter() - t0, text, len(frames),
                       {"answer_mass": round(total, 4), **extra})
