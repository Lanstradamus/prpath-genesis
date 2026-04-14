"""
Create the Gen 0 population.json for PRPath GENESIS.

Converts the 12 hand-picked carousels (already generated) into the
genome format the evolution engine expects.
"""
import json
from datetime import date
from pathlib import Path

EVOLUTION_ROOT = Path(__file__).parent
GENS_DIR = EVOLUTION_ROOT / "generations"
GEN0_DIR = GENS_DIR / "gen_000"
GEN0_DIR.mkdir(parents=True, exist_ok=True)


# Mirror of generate_gen0.py carousels
GEN0_CAROUSELS = [
    {"id": "gen000_01_squat_180",       "type": "score_rank",  "exercise": "squat",           "bw": "180"},
    {"id": "gen000_02_bench_180",       "type": "score_rank",  "exercise": "bench_press",     "bw": "180"},
    {"id": "gen000_03_deadlift_180",    "type": "score_rank",  "exercise": "deadlift",        "bw": "180"},
    {"id": "gen000_04_ohp_180",         "type": "score_rank",  "exercise": "overhead_press",  "bw": "180"},
    {"id": "gen000_05_bodymap_breakup", "type": "body_map_progress", "exercise": None,        "bw": "180"},
    {"id": "gen000_06_bodymap_consistency", "type": "body_map_progress", "exercise": None,    "bw": "180"},
    {"id": "gen000_07_squat_150",       "type": "score_rank",  "exercise": "squat",           "bw": "150"},
    {"id": "gen000_08_bench_150",       "type": "score_rank",  "exercise": "bench_press",     "bw": "150"},
    {"id": "gen000_09_hipthrust_150",   "type": "score_rank",  "exercise": "hip_thrust",      "bw": "150"},
    {"id": "gen000_10_pullup_180",      "type": "score_rank",  "exercise": "pull_up",         "bw": "180"},
    {"id": "gen000_11_row_180",         "type": "score_rank",  "exercise": "barbell_row",     "bw": "180"},
    {"id": "gen000_12_frontsquat_200",  "type": "score_rank",  "exercise": "front_squat",     "bw": "200"},
]


def build_genome(c: dict) -> dict:
    structural = {
        "content_format": c["type"],
        "slide_count": 4 if c["type"] == "body_map_progress" else 7,
        "background_style": "solid_dark",
        "cover_style": "accent_word",
        "cta_style": "app_store_badge",
        "slide_duration_s": 3.0,
        "posting_time": "midday_12pm",
    }

    content = {
        "hook_framework": "whats_your_score" if c["type"] == "score_rank" else "pov_consistency",
        "accent_color_gene": "orange",
        "caption_tone": "comment_bait",
        "hashtag_strategy": "niche_5",
    }
    if c.get("exercise"):
        content["exercise_topic"] = c["exercise"]
    if c.get("bw"):
        content["bodyweight_ref"] = c["bw"]
    content["gender_framing"] = "unisex"

    return {
        "genome_id": c["id"],
        "template": "PRPathCarousel",
        "generation": 0,
        "pool": "seed",
        "parent_ids": [],
        "lineage_type": "seed",
        "structural_genes": structural,
        "content_genes": content,
        "status": "posted",  # Will update to "measured" after metrics pulled
    }


def main():
    population = [build_genome(c) for c in GEN0_CAROUSELS]

    gen0_data = {
        "generation": 0,
        "spawned_at": str(date.today()),
        "spawn_mode": "seed",
        "population": population,
    }

    out_path = GEN0_DIR / "population.json"
    out_path.write_text(json.dumps(gen0_data, indent=2), encoding="utf-8")

    print(f"[gen0] Created {len(population)} genomes")
    print(f"[gen0] Saved to {out_path}")

    # Also create empty supporting files so evolve.py has them to append to
    memory_path = EVOLUTION_ROOT / "memory.jsonl"
    if not memory_path.exists():
        memory_path.touch()
        print(f"[gen0] Created empty memory.jsonl")

    hof_path = EVOLUTION_ROOT / "hall_of_fame.json"
    if not hof_path.exists():
        hof_path.write_text(json.dumps({
            "updated": str(date.today()),
            "max_size": 10,
            "entries": []
        }, indent=2), encoding="utf-8")
        print(f"[gen0] Created empty hall_of_fame.json")

    blacklist_path = EVOLUTION_ROOT / "blacklist.json"
    if not blacklist_path.exists():
        blacklist_path.write_text(json.dumps({
            "updated": str(date.today()),
            "rules": {
                "kill_after_exposures": 3,
                "kill_after_bottom_percentile": 0.25,
                "kill_after_consecutive_gens": 3,
            },
            "banned": {"prpath_carousel": {}}
        }, indent=2), encoding="utf-8")
        print(f"[gen0] Created empty blacklist.json")


if __name__ == "__main__":
    main()
