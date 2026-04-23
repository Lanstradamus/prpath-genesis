"""PRPath caption drafter — deterministic caption generation from the locked
Week-2 v2.1 plan. No Claude API, no LLM calls.

Input:  post_id (one of the 18 v2.1 posts), optional variant seed
Output: (caption_body, hashtag_line) tuple

Locked rules:
- Hevy template: "X isn't/aren't [pain] if you download PRPath 💪"
- Hashtag set: #gymtok #strengthtraining #<gymbro|gymgirl> #prpath
- #gymgirl for posts in WOMEN_ANGLED (#11, #17, #18) or female-voiced variants
- No #fyp / #foryou / #viral / generic #fitness
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Iterable


# ---------------------------------------------------------------------------
# Feature-anchor → post metadata (hook pain, caption seed)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PostSpec:
    post_id: str
    anchor: str
    women_angled: bool
    captions: tuple[str, ...]  # Hevy variants to rotate through


# All 18 posts from [[PRPath - Week 2 Content Strategy]] v2.1, each with
# 3-5 variants of the Hevy template so regeneration feels fresh.
INVENTORY: dict[str, PostSpec] = {
    "01_atlas_skip_leg": PostSpec("01_atlas_skip_leg", "A", False, (
        "Putting off leg day until tomorrow isn't the move if you download PRPath 💪",
        "Skipping legs isn't a free win if you download PRPath 💪",
        "\"I'll hit legs tomorrow\" isn't a strategy if you download PRPath 💪",
    )),
    "02_notes_mess": PostSpec("02_notes_mess", "B", False, (
        "Logging workouts isn't messy if you download PRPath 💪",
        "Your lifting log isn't stuck in iPhone Notes if you download PRPath 💪",
        "Tracking what you squatted 3 weeks ago isn't guesswork if you download PRPath 💪",
    )),
    "03_chest_fried": PostSpec("03_chest_fried", "B", True, (
        "Benching through a fried chest isn't progress if you download PRPath 💪",
        "Wondering why your glutes aren't growing isn't a mystery if you download PRPath 💪",
        "Hammering the same muscles isn't the fix if you download PRPath 💪",
    )),
    "04_rest_or_push": PostSpec("04_rest_or_push", "B", False, (
        "Sore-or-push isn't a guess if you download PRPath 💪",
        "\"Rest today?\" isn't a toss-up if you download PRPath 💪",
        "Your body's recovery signal isn't invisible if you download PRPath 💪",
    )),
    "05_program_ignores": PostSpec("05_program_ignores", "B", False, (
        "Your cookie-cutter program isn't blind to yesterday if you download PRPath 💪",
        "Squatting right after destroying quads isn't the plan if you download PRPath 💪",
        "A program that ignores your fatigue isn't programming if you download PRPath 💪",
    )),
    "06_four_weeks_bench": PostSpec("06_four_weeks_bench", "C", False, (
        "Four weeks of benching isn't vibes if you download PRPath 💪",
        "Same weight every session isn't progress if you download PRPath 💪",
        "Training without data isn't lifting if you download PRPath 💪",
    )),
    "07_pr_thursday": PostSpec("07_pr_thursday", "C", False, (
        "Remembering when you PR'd isn't up to you if you download PRPath 💪",
        "\"Was that PR Thursday or Saturday?\" isn't a question if you download PRPath 💪",
        "Your PR history isn't guesswork if you download PRPath 💪",
    )),
    "08_proof_not_hype": PostSpec("08_proof_not_hype", "C", False, (
        "I'm scared I'm not actually getting stronger.",  # emotional-universal v2.1
        "Not knowing if you're progressing isn't acceptable if you download PRPath 💪",
        "Four weeks of work is either working or it isn't — the graph tells you. PRPath 💪",
    )),
    "09_185_at_180": PostSpec("09_185_at_180", "D", False, (
        "Your bench number isn't unranked if you download PRPath 💪",
        "\"Is 185 at 180 good?\" isn't a mystery if you download PRPath 💪",
        "Your lift tier isn't guesswork if you download PRPath 💪",
    )),
    "10_where_rank": PostSpec("10_where_rank", "D", False, (
        "Three years of lifting without a tier isn't normal if you download PRPath 💪",
        "Not knowing where you stand isn't acceptable if you download PRPath 💪",
        "Your percentile isn't a vibe if you download PRPath 💪",
    )),
    "11_mfp_tedious": PostSpec("11_mfp_tedious", "E", True, (
        "Tracking macros on a cut isn't 7 minutes per meal if you download PRPath 💪",
        "Quitting your cut by Wednesday isn't the move if you download PRPath 💪",
        "Macro tracking isn't a spreadsheet if you download PRPath 💪",
    )),
    "12_overtraining_fear": PostSpec("12_overtraining_fear", "B", False, (
        "I'm scared I'm overtraining without knowing it.",  # emotional-universal v2.1
        "Silent overtraining isn't your fate if you download PRPath 💪",
        "Guessing your recovery isn't a plan if you download PRPath 💪",
    )),
    "13_protein_guess": PostSpec("13_protein_guess", "E", False, (
        "Guessing your protein isn't hitting 180g if you download PRPath 💪",
        "\"I think I hit 180g\" isn't tracking if you download PRPath 💪",
        "Eyeballing protein isn't progress if you download PRPath 💪",
    )),
    "14_decide_gym": PostSpec("14_decide_gym", "F", False, (
        "Standing at the gym deciding what to train isn't 10 min gone if you download PRPath 💪",
        "Wasting warm-up time deciding isn't the move if you download PRPath 💪",
        "Walking into the gym without a plan isn't the plan if you download PRPath 💪",
    )),
    "15_template_pdf": PostSpec("15_template_pdf", "F", False, (
        "Your program isn't a PDF if you download PRPath 💪",
        "A program that ignores your history isn't a program if you download PRPath 💪",
        "Static PDF templates aren't training plans if you download PRPath 💪",
    )),
    "16_what_to_train": PostSpec("16_what_to_train", "F", False, (
        "What to train today isn't a toss-up if you download PRPath 💪",
        "Picking your workout isn't 10 min of stress if you download PRPath 💪",
        "\"What should I train?\" isn't unanswered if you download PRPath 💪",
    )),
    "17_leaner_stronger": PostSpec("17_leaner_stronger", "G", True, (
        "Getting leaner AND stronger isn't one at a time if you download PRPath 💪",
        "Cutting AND lifting aren't mutually exclusive if you download PRPath 💪",
        "You don't have to pick — strength + leaner both, if you download PRPath 💪",
    )),
    "18_first_pr": PostSpec("18_first_pr", "G", True, (
        "Your first PR isn't less huge than your 50th if you download PRPath 💪",
        "135 or 225, the app celebrates both — if you download PRPath 💪",
        "Progression isn't gatekept if you download PRPath 💪",
    )),
}

TAGS_DEFAULT = "#gymtok #strengthtraining #gymbro #prpath"
TAGS_WOMEN   = "#gymtok #strengthtraining #gymgirl #prpath"


def _variant_index(post_id: str, seed: int, num_variants: int) -> int:
    """Deterministically pick a variant. seed=0 = always first; seed>0 = shuffle."""
    if seed <= 0:
        return 0
    key = f"{post_id}:{seed}".encode()
    h = int.from_bytes(hashlib.sha1(key).digest()[:4], "big")
    return h % num_variants


def draft_caption(post_id: str, variant_seed: int = 0) -> tuple[str, str]:
    """Return (caption_body, hashtag_line) for a post.

    variant_seed=0 → default (first variant). Pass a new int to reroll.
    """
    spec = INVENTORY.get(post_id)
    if not spec:
        # Fallback for unknown posts — generic
        return (f"A smarter way to train — if you download PRPath 💪", TAGS_DEFAULT)

    idx = _variant_index(post_id, variant_seed, len(spec.captions))
    caption = spec.captions[idx]
    tags = TAGS_WOMEN if spec.women_angled else TAGS_DEFAULT
    return (caption, tags)


def full_caption(post_id: str, variant_seed: int = 0) -> str:
    """Return the full caption string (body + blank line + tags)."""
    body, tags = draft_caption(post_id, variant_seed)
    return f"{body}\n\n{tags}"


def draft_all_for_batch(slot_post_ids: Iterable[str], seed_base: int = 0) -> dict[str, str]:
    """Return a dict of {post_id: full_caption} for a batch's slots."""
    return {pid: full_caption(pid, variant_seed=seed_base) for pid in slot_post_ids}


if __name__ == "__main__":
    # Quick smoke test: draft all 18 with default seed
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    for post_id in INVENTORY:
        print(f"\n{post_id}")
        print(full_caption(post_id, variant_seed=args.seed))
