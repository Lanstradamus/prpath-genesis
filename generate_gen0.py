"""
Generate Gen 0 — The first 12 carousels for PRPath GENESIS.

Covers all content categories with hand-picked genomes:
- 4x Score/Rank (squat, bench, deadlift, OHP) — 33%
- 2x Body Map (before/after progress) — 17%
- 3x Score/Rank with different bodyweights/exercises — 25%
- 1x Score/Rank female-focused (hip thrust) — 8%
- 2x Score/Rank variety (pull-up, front squat) — 17%

Run: python -X utf8 generate_gen0.py
"""

import json
import time
from pathlib import Path
from app_screen_renderer import AppScreenRenderer
from carousel_generator import generate_score_rank_carousel, generate_body_map_carousel

SLIDES_DIR = Path(__file__).parent / "slides" / "gen_000"


def main():
    print("PRPath GENESIS — Generating Gen 0")
    print("=" * 50)
    start = time.time()

    gen0_carousels = [
        # Core Score/Rank — Big 3 + OHP at 180lbs
        {"type": "score_rank", "exercise": "squat", "bodyweight": "180", "id": "gen000_01_squat_180"},
        {"type": "score_rank", "exercise": "bench_press", "bodyweight": "180", "id": "gen000_02_bench_180"},
        {"type": "score_rank", "exercise": "deadlift", "bodyweight": "180", "id": "gen000_03_deadlift_180"},
        {"type": "score_rank", "exercise": "overhead_press", "bodyweight": "180", "id": "gen000_04_ohp_180"},

        # Body Map Progress
        {"type": "body_map", "title": "I won the breakup.", "id": "gen000_05_bodymap_breakup"},
        {"type": "body_map", "title": "6 months of consistency.", "id": "gen000_06_bodymap_consistency"},

        # Score/Rank at 150lbs (lighter lifters / female audience)
        {"type": "score_rank", "exercise": "squat", "bodyweight": "150", "id": "gen000_07_squat_150"},
        {"type": "score_rank", "exercise": "bench_press", "bodyweight": "150", "id": "gen000_08_bench_150"},

        # Female-focused (hip thrust is the #1 female exercise)
        {"type": "score_rank", "exercise": "hip_thrust", "bodyweight": "150", "id": "gen000_09_hipthrust_150"},

        # Variety exercises
        {"type": "score_rank", "exercise": "pull_up", "bodyweight": "180", "id": "gen000_10_pullup_180"},
        {"type": "score_rank", "exercise": "barbell_row", "bodyweight": "180", "id": "gen000_11_row_180"},
        {"type": "score_rank", "exercise": "front_squat", "bodyweight": "200", "id": "gen000_12_frontsquat_200"},
    ]

    SLIDES_DIR.mkdir(parents=True, exist_ok=True)

    with AppScreenRenderer() as renderer:
        for i, genome in enumerate(gen0_carousels):
            genome_id = genome["id"]
            output_dir = SLIDES_DIR / genome_id
            print(f"\n[{i+1}/12] {genome_id}")

            if genome["type"] == "score_rank":
                slides = generate_score_rank_carousel(
                    renderer,
                    genome["exercise"],
                    genome["bodyweight"],
                    output_dir=str(output_dir),
                )
            elif genome["type"] == "body_map":
                slides = generate_body_map_carousel(
                    renderer,
                    title=genome["title"],
                    output_dir=str(output_dir),
                )

            print(f"  {len(slides)} slides generated")

    elapsed = time.time() - start
    print(f"\n{'=' * 50}")
    print(f"Gen 0 complete: 12 carousels in {elapsed:.1f}s")
    print(f"Output: {SLIDES_DIR}")

    # Save manifest
    manifest = {
        "generation": 0,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "carousels": gen0_carousels,
    }
    with open(SLIDES_DIR / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest saved: {SLIDES_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()
