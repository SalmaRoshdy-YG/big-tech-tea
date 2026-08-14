"""Turn an abstract into a short structured brief.

Two backends:

  heuristic (default, free, no network)
      Splits the abstract into sentences and sorts them into the five slots a
      reader actually wants. It reorganises the authors' own words -- it does
      not understand the paper. If the abstract never says how the work differs
      from prior literature, this cannot invent it, and it will say so.

  llm (opt-in, needs an API key)
      Any OpenAI-compatible /chat/completions endpoint. Falls back to the
      heuristic on any error, and stops after LLM_MAX_CALLS per run so a free
      tier stays free.

Summaries are cached by paper uid in state/summaries.json, so each paper costs
at most one call ever.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

SLOTS = ("problem", "prior_work", "approach", "results", "next_steps")
DISPLAY_SLOTS = ("overview",) + SLOTS

LABELS = {
    "overview": "In brief",
    "problem": "Problem",
    "prior_work": "vs. prior work",
    "approach": "What they did",
    "results": "Results",
    "next_steps": "Limits / next",
}

CUES: dict[str, tuple[str, ...]] = {
    "problem": (
        "we address", "we study", "we investigate", "the problem of", "the task of",
        "challenge", "challenging", "difficult", "bottleneck", "remains", "struggle",
        "suffer", "expensive", "costly", "however", "unfortunately", "limitation",
        "is hard", "requires", "motivated by",
    ),
    "prior_work": (
        "prior work", "prior system", "prior method", "prior approach", "prior model",
        "previous work", "previously", "existing", "unlike", "in contrast",
        "compared to", "whereas", "traditionally", "conventional", "state-of-the-art",
        "state of the art", "recent work", "earlier", "baseline", "differ",
    ),
    "approach": (
        "we propose", "we introduce", "we present", "we develop", "we design",
        "we train", "we build", "we describe", "this paper", "in this work",
        "our method", "our approach", "our model", "our framework", "we use",
        "we apply", "we combine", "we fine-tune",
    ),
    "results": (
        "we show", "we find", "we demonstrate", "results", "achiev", "outperform",
        "improv", "reduc", "gains", "accuracy", "score", "benchmark", "surpass",
        "yields", "boost", "% ", "percent", "x faster", "speedup", "ablation",
    ),
    "next_steps": (
        "future work", "future research", "we hope", "open question", "open problem",
        "remains open", "limitation", "caveat", "we release", "code is available",
        "code is released", "we open-source", "available at", "publicly available",
        "we make", "leave", "further work", "does not yet", "still fails",
    ),
}

_ABBREV = r"(?<!\be\.g)(?<!\bi\.e)(?<!\bal)(?<!\bFig)(?<!\bvs)(?<!\bEq)(?<!\bcf)(?<!\bapprox)"
_SENT_SPLIT = re.compile(rf"{_ABBREV}(?<=[.!?])\s+(?=[A-Z(])")
_LEAD_CONNECTIVE = re.compile(
    r"^(however|moreover|furthermore|in addition|additionally|finally|thus|"
    r"therefore|specifically|in particular|to this end|as a result)[,:]?\s+",
    re.I,
)


@dataclass
class Brief:
    overview: str = ""
    problem: str = ""
    prior_work: str = ""
    approach: str = ""
    results: str = ""
    next_steps: str = ""
    mode: str = "heuristic"
    notes: list[str] = field(default_factory=list)

    def filled(self) -> list[tuple[str, str]]:
        return [(LABELS[s], getattr(self, s)) for s in DISPLAY_SLOTS if getattr(self, s)]

    def as_bullets(self, width: int = 190) -> str:
        lines = [f"- {label}: {_shorten(text, width)}" for label, text in self.filled()]
        if self.notes:
            lines.append(f"- ({'; '.join(self.notes)})")
        return "\n".join(lines) if lines else "No abstract available in the feed."

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Brief":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _shorten(text: str, width: int) -> str:
    text = text.strip()
    if len(text) <= width:
        return text
    return text[: width - 1].rsplit(" ", 1)[0] + "\u2026"


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    return [s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) > 25]


def _score(sentence: str, slot: str) -> int:
    lowered = sentence.lower()
    return sum(2 if cue in lowered[:60] else 1
               for cue in CUES[slot] if cue in lowered)


def heuristic_brief(title: str, abstract: str) -> Brief:
    sentences = split_sentences(abstract)
    brief = Brief(mode="heuristic")

    if not sentences:
        if (abstract or "").strip():
            brief.overview = re.sub(r"\s+", " ", abstract.strip())[:300]
            brief.notes.append("one-line feed summary; open the page for detail")
        else:
            brief.notes.append("feed gave no abstract; open the paper for detail")
        return brief

    # Announcement posts and one-line feed blurbs do not follow the
    # problem/method/results rhetoric of an abstract. Forcing them into those
    # slots produces confident nonsense, so say what it is and stop.
    cue_total = sum(_score(s, slot) for s in sentences for slot in SLOTS)
    if len(sentences) < 3 or cue_total < 4:
        brief.overview = " ".join(sentences[:2])
        brief.notes.append("short feed blurb, not a structured abstract")
        return brief

    assigned: dict[str, list[str]] = {slot: [] for slot in SLOTS}
    unscored: list[tuple[int, str]] = []

    for index, sentence in enumerate(sentences):
        # Abstracts open with context unless they open by announcing the work.
        if index == 0 and not re.match(
            r"^(we|this paper|in this (paper|work)|our)\b", sentence, re.I
        ):
            assigned["problem"].append(sentence)
            continue

        # On a tie, a sentence late in the abstract belongs to a late slot.
        position = index / max(1, len(sentences) - 1)
        order = list(reversed(SLOTS)) if position > 0.6 else list(SLOTS)
        scores = {slot: _score(sentence, slot) for slot in SLOTS}
        best = max(order, key=lambda s: scores[s])
        if scores[best] == 0:
            unscored.append((index, sentence))
        else:
            assigned[best].append(_LEAD_CONNECTIVE.sub("", sentence))

    # Sentences with no cue words fall back to position: abstracts open with
    # motivation and close with findings.
    total = len(sentences)
    for index, sentence in unscored:
        position = index / max(1, total - 1)
        slot = "problem" if position < 0.34 else ("approach" if position < 0.7 else "results")
        assigned[slot].append(_LEAD_CONNECTIVE.sub("", sentence))

    used: set[str] = set()
    for slot in SLOTS:
        if assigned[slot]:
            kept = [s for s in assigned[slot] if s not in used][:2]
            used.update(kept)
            if kept:
                setattr(brief, slot, " ".join(kept))

    if not brief.problem and sentences[0] not in used:
        brief.problem = sentences[0]
    if not brief.prior_work:
        brief.notes.append("abstract doesn't compare to prior work")
    return brief


# --------------------------------------------------------------------------
# Optional LLM backend
# --------------------------------------------------------------------------

PROMPT = """You are summarising a research paper for a graduate student who is \
deciding whether to read it. You are given only the title and abstract.

Reply with JSON only, no markdown fence, with exactly these keys:
"problem", "prior_work", "approach", "results", "next_steps".

Each value is ONE plain sentence, under 30 words, in simple language.
Use an empty string for anything the abstract does not state. Never guess: if \
the abstract does not say how this differs from earlier work, "prior_work" must \
be "".

Title: {title}

Abstract: {abstract}"""


class LLMConfig:
    def __init__(self):
        self.base_url = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        self.model = os.environ.get("LLM_MODEL", "")
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.max_calls = int(os.environ.get("LLM_MAX_CALLS", "25"))
        self.timeout = int(os.environ.get("LLM_TIMEOUT", "45"))

    @property
    def usable(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)


def llm_brief(title: str, abstract: str, cfg: LLMConfig) -> Brief | None:
    payload = {
        "model": cfg.model,
        "messages": [{"role": "user",
                      "content": PROMPT.format(title=title, abstract=abstract[:6000])}],
        "temperature": 0.2,
        "max_tokens": 400,
    }
    request = urllib.request.Request(
        f"{cfg.base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg.api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=cfg.timeout) as resp:
            body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
        data = json.loads(content)
    except (urllib.error.URLError, KeyError, IndexError, ValueError, TimeoutError) as exc:
        print(f"    ! summarizer fell back to heuristic: {type(exc).__name__}: {exc}")
        return None

    brief = Brief(mode="llm")
    for slot in SLOTS:
        value = data.get(slot)
        if isinstance(value, str):
            setattr(brief, slot, value.strip())
    if not any(getattr(brief, slot) for slot in SLOTS):
        return None
    if not brief.prior_work:
        brief.notes.append("abstract doesn't compare to prior work")
    return brief


class Summarizer:
    """Caches briefs by paper uid so each paper is summarised at most once."""

    def __init__(self, cache_path, mode: str | None = None):
        self.path = cache_path
        self.mode = mode or os.environ.get("SUMMARIZER", "heuristic")
        self.cfg = LLMConfig()
        self.calls = 0
        self.cache: dict[str, dict] = {}
        if cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(f"! summary cache {cache_path} is corrupt; starting fresh")
        if self.mode == "llm" and not self.cfg.usable:
            print("! SUMMARIZER=llm but LLM_BASE_URL/LLM_MODEL/LLM_API_KEY are not all "
                  "set; using the heuristic summariser")
            self.mode = "heuristic"

    def brief_for(self, paper) -> Brief:
        cached = self.cache.get(paper.uid)
        if cached:
            return Brief.from_dict(cached)

        abstract = paper.summary or ""
        brief = None
        if self.mode == "llm" and self.calls < self.cfg.max_calls and len(abstract) > 120:
            self.calls += 1
            brief = llm_brief(paper.title, abstract, self.cfg)
        if brief is None:
            brief = heuristic_brief(paper.title, abstract)

        self.cache[paper.uid] = brief.to_dict()
        return brief

    def save(self, keep_uids: set[str] | None = None) -> None:
        if keep_uids is not None:
            self.cache = {k: v for k, v in self.cache.items() if k in keep_uids}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
