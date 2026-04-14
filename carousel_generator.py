"""
PRPath GENESIS Carousel Generator

Takes a genome (JSON) and generates a complete carousel as ordered PNG slides.
Routes to HTML template screenshots (for app-looking slides) or Pillow
rendering (for text/graphic slides).

Usage:
    python -X utf8 carousel_generator.py --exercise squat --bodyweight 180
    python -X utf8 carousel_generator.py --genome path/to/genome.json
    python -X utf8 carousel_generator.py --test
"""

import json
import argparse
import os
from pathlib import Path
from app_screen_renderer import AppScreenRenderer
from pillow_slides import (
    cover_hook_slide, numbered_slide, text_slide,
    comparison_slide, stat_shock_slide,
)

DATA_DIR = Path(__file__).parent / "data"
SLIDES_DIR = Path(__file__).parent / "slides"

# Tier data matching PRPath StrengthStandards.swift exactly
TIERS = [
    {"name": "Beginner",     "range": (0, 16),   "color": "#FF3B30", "desc": "Just starting out"},
    {"name": "Novice",       "range": (17, 33),   "color": "#FF9500", "desc": "Building foundation"},
    {"name": "Intermediate", "range": (34, 50),  "color": "#FFCC00", "desc": "Solid progress"},
    {"name": "Advanced",     "range": (51, 67),  "color": "#34C759", "desc": "Above average"},
    {"name": "Elite",        "range": (68, 84),  "color": "#00B0FF", "desc": "Top 10%"},
    {"name": "World Class",  "range": (85, 100), "color": "#AF52DE", "desc": "Top 1%"},
]


def get_tier(score):
    for t in TIERS:
        if t["range"][0] <= score <= t["range"][1]:
            return t
    return TIERS[0]


def load_exercise_data():
    with open(DATA_DIR / "exercise_data.json", "r", encoding="utf-8") as f:
        return json.load(f)


def generate_score_rank_carousel(
    renderer: AppScreenRenderer,
    exercise_key: str,
    bodyweight: str = "180",
    gender: str = "male",
    output_dir: str = None,
) -> list[str]:
    """
    Generate a Score/Rank carousel for a given exercise.

    Slides:
      1. Cover slide (hook)
      2-5. Tier cards (Beginner through Elite or World Class)
      6. Strength score gauge (at Advanced level as aspirational)
      7. CTA slide

    Returns list of PNG file paths.
    """
    data = load_exercise_data()
    exercise = data["exercises"].get(exercise_key)
    if not exercise:
        raise ValueError(f"Unknown exercise: {exercise_key}. Available: {list(data['exercises'].keys())}")

    display_name = exercise["display_name"]
    bw_examples = exercise["bodyweight_examples"].get(bodyweight, exercise["bodyweight_examples"]["180"])
    tiers_data = exercise["tiers"]

    # Output directory
    if not output_dir:
        output_dir = SLIDES_DIR / f"score_rank_{exercise_key}_{bodyweight}lbs"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    slides = []

    # Slide 1: Cover
    cover_path = str(output_dir / "slide_01_cover.png")
    renderer.screenshot("cover_slide", {
        "hook_text": f"What's your {display_name.upper()} SCORE?",
        "accent_word": display_name.upper(),
        "show_gauge_preview": "true",
    }, cover_path)
    slides.append(cover_path)

    # Slides 2-5: Tier cards (pick 4 tiers to show)
    tier_selection = ["beginner", "intermediate", "advanced", "elite"]
    for i, tier_key in enumerate(tier_selection):
        tier_info = next(t for t in TIERS if t["name"].lower().replace(" ", "") == tier_key.replace("_", "").replace(" ", ""))
        tier_data = tiers_data[tier_key]
        weight_example = bw_examples[tier_key]

        slide_path = str(output_dir / f"slide_{i+2:02d}_tier_{tier_key}.png")
        renderer.screenshot("tier_card", {
            "tier": tier_info["name"],
            "score": str(tier_data["score"]),
            "exercise": display_name,
            "multiplier": f'{tier_data["multiplier"]} bodyweight',
            "weight_example": f'{weight_example} / {bodyweight} lbs BW',
            "tier_subtitle": tier_info["desc"],
            "gauge_color": tier_info["color"],
            "bodyweight_ref": f'{bodyweight} lbs',
            "show_gauge": "true",
        }, slide_path)
        slides.append(slide_path)

    # Slide 6: Strength score gauge (show Advanced level as aspirational)
    adv_tier = tiers_data["advanced"]
    gauge_path = str(output_dir / "slide_06_gauge.png")
    renderer.screenshot("strength_score", {
        "score": str(adv_tier["score"]),
        "exercise": display_name,
        "bodyweight": f'{bodyweight} lbs',
        "weight_lifted": bw_examples["advanced"],
        "multiplier": adv_tier["multiplier"],
    }, gauge_path)
    slides.append(gauge_path)

    # Slide 7: CTA
    cta_path = str(output_dir / "slide_07_cta.png")
    renderer.screenshot("cta_slide", {
        "tagline": "Track your strength score",
        "show_app_store_badge": "true",
    }, cta_path)
    slides.append(cta_path)

    return slides


def generate_listicle_carousel(
    renderer: AppScreenRenderer,
    topic_key: str = None,
    title: str = None,
    items: list = None,
    cta_tagline: str = "Track every set with PRPath",
    output_dir: str = None,
) -> list[str]:
    """
    Generate a Numbered Listicle carousel (D1 format).
    "5 reasons you're stuck at the same weight" style.

    Slides:
      1. Cover hook
      2-N. Numbered points (one per slide)
      N+1. CTA slide
    """
    # Load from content bank if topic_key provided
    if topic_key and not items:
        with open(DATA_DIR / "content_bank.json", "r", encoding="utf-8") as f:
            bank = json.load(f)
        topics = bank.get("listicle_topics", [])
        topic = next((t for t in topics if topic_key in t.get("title", "").lower()), None)
        if topic:
            title = topic["title"]
            items = topic["slides"]
            cta_tagline = topic.get("cta", cta_tagline)

    if not title or not items:
        raise ValueError("Need title + items OR topic_key from content_bank.json")

    if not output_dir:
        safe_key = (topic_key or title.lower().replace(" ", "_"))[:50]
        output_dir = SLIDES_DIR / f"listicle_{safe_key}"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    slides = []

    # Slide 1: Cover hook — find accent word (usually the number)
    import re
    nums = re.findall(r"\d+", title)
    accent = nums[0] if nums else None

    cover_path = str(output_dir / "slide_01_cover.png")
    cover_hook_slide(hook_text=title, accent_word=accent, output_path=cover_path)
    slides.append(cover_path)

    # Slides 2 to N+1: numbered items
    total = len(items)
    for i, item in enumerate(items, 1):
        slide_path = str(output_dir / f"slide_{i+1:02d}_num.png")
        # Support both string items and dict items with body text
        if isinstance(item, dict):
            numbered_slide(
                num=i, title=item.get("title", ""), body=item.get("body"),
                total=total, output_path=slide_path,
            )
        else:
            numbered_slide(num=i, title=item, total=total, output_path=slide_path)
        slides.append(slide_path)

    # Final slide: CTA (use HTML CTA template via Playwright renderer)
    cta_path = str(output_dir / f"slide_{total+2:02d}_cta.png")
    renderer.screenshot("cta_slide", {
        "tagline": cta_tagline, "show_app_store_badge": "true",
    }, cta_path)
    slides.append(cta_path)

    return slides


def generate_body_map_carousel(
    renderer: AppScreenRenderer,
    title: str = "I won the breakup.",
    output_dir: str = None,
) -> list[str]:
    """
    Generate a Before/After body map carousel (Stronger's viral format).

    Slides:
      1. Cover slide (emotional hook)
      2. Body map "Before" (mostly gray, few muscles lit)
      3. Body map "After" (fully lit, colorful)
      4. CTA slide
    """
    if not output_dir:
        output_dir = SLIDES_DIR / "body_map_progress"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    slides = []

    # Slide 1: Cover
    cover_path = str(output_dir / "slide_01_cover.png")
    renderer.screenshot("cover_slide", {
        "hook_text": title,
        "accent_word": title.split()[0] if title else "I",
    }, cover_path)
    slides.append(cover_path)

    # Slide 2: Before body map (sparse)
    before_path = str(output_dir / "slide_02_before.png")
    renderer.screenshot("muscle_body_map", {
        "view": "front",
        "title": "Start",
        "chest_color": "#FF3B30",
        "chest_intensity": "0.3",
        "biceps_color": "#FF9500",
        "biceps_intensity": "0.2",
    }, before_path)
    slides.append(before_path)

    # Slide 3: After body map (fully lit)
    after_path = str(output_dir / "slide_03_after.png")
    renderer.screenshot("muscle_body_map", {
        "view": "front",
        "title": "Now",
        "chest_color": "#00B0FF",
        "chest_intensity": "0.9",
        "shoulders_color": "#00B0FF",
        "shoulders_intensity": "0.8",
        "biceps_color": "#FF9500",
        "biceps_intensity": "0.7",
        "abs_color": "#34C759",
        "abs_intensity": "0.6",
        "quads_color": "#AF52DE",
        "quads_intensity": "0.9",
        "calves_color": "#FFCC00",
        "calves_intensity": "0.5",
    }, after_path)
    slides.append(after_path)

    # Slide 4: CTA
    cta_path = str(output_dir / "slide_04_cta.png")
    renderer.screenshot("cta_slide", {
        "tagline": "See your muscle progress",
        "show_app_store_badge": "true",
    }, cta_path)
    slides.append(cta_path)

    return slides


def main():
    parser = argparse.ArgumentParser(description="PRPath GENESIS Carousel Generator")
    parser.add_argument("--exercise", type=str, help="Exercise key (e.g. squat, bench_press)")
    parser.add_argument("--bodyweight", type=str, default="180", help="Reference bodyweight (150, 180, 200)")
    parser.add_argument("--type", type=str, default="score_rank", choices=["score_rank", "body_map"], help="Carousel type")
    parser.add_argument("--test", action="store_true", help="Generate test carousels for all types")
    parser.add_argument("--output", type=str, help="Output directory override")
    args = parser.parse_args()

    print("PRPath GENESIS Carousel Generator")
    print("=" * 40)

    with AppScreenRenderer() as renderer:
        if args.test:
            print("\nGenerating test carousels...\n")

            # Test 1: Squat Score/Rank
            slides = generate_score_rank_carousel(renderer, "squat", "180")
            print(f"Score/Rank (Squat, 180lbs): {len(slides)} slides")
            for s in slides:
                print(f"  {s}")

            # Test 2: Bench Press Score/Rank
            slides = generate_score_rank_carousel(renderer, "bench_press", "180")
            print(f"\nScore/Rank (Bench Press, 180lbs): {len(slides)} slides")
            for s in slides:
                print(f"  {s}")

            # Test 3: Body Map Progress
            slides = generate_body_map_carousel(renderer, "I won the breakup.")
            print(f"\nBody Map Progress: {len(slides)} slides")
            for s in slides:
                print(f"  {s}")

            # Test 4: Deadlift Score/Rank at 150lbs
            slides = generate_score_rank_carousel(renderer, "deadlift", "150")
            print(f"\nScore/Rank (Deadlift, 150lbs): {len(slides)} slides")
            for s in slides:
                print(f"  {s}")

            print(f"\nDone! All slides saved to: {SLIDES_DIR}")

        elif args.exercise:
            if args.type == "score_rank":
                slides = generate_score_rank_carousel(
                    renderer, args.exercise, args.bodyweight, output_dir=args.output
                )
            elif args.type == "body_map":
                slides = generate_body_map_carousel(renderer, output_dir=args.output)

            print(f"Generated {len(slides)} slides:")
            for s in slides:
                print(f"  {s}")
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
