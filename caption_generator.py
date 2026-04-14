"""
PRPath GENESIS Caption Generator — Platform-optimized metadata for each carousel.

2026 Best Practices (researched Apr 13, 2026):
- TikTok: 200+ char keyword-rich captions, 3-5 mixed hashtags, comment bait hook
- YouTube Shorts: Keyword-first title (60 chars visible), description with 3-5 hashtags, #Shorts required
- Instagram: Keywords > hashtags, first 125 chars = visible hook, 3-5 niche hashtags at end
- Facebook: Keyword-rich description, aligned with on-screen text, shares = #1 signal

Usage:
    from caption_generator import generate_captions
    captions = generate_captions("squat", "score_rank", "gen000_01_squat_180", bodyweight="180")
"""

import json
import random
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def _load_content_bank():
    with open(DATA_DIR / "content_bank.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _load_exercise_data():
    with open(DATA_DIR / "exercise_data.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ─── Hashtag Banks ──────────────────────────────────────────────────

HASHTAGS = {
    "broad_fitness": ["#gymtok", "#fitness", "#gym", "#workout", "#fitnessmotivation"],
    "niche_strength": ["#strengthscore", "#strengthtraining", "#progressiveoverload", "#gymtracker", "#liftinglife"],
    "exercise_specific": {
        "squat": ["#squatcheck", "#squatday", "#legday", "#squats"],
        "bench_press": ["#benchpress", "#chestday", "#bench", "#benchday"],
        "deadlift": ["#deadlift", "#deadliftday", "#backday", "#pulls"],
        "overhead_press": ["#ohp", "#shoulderday", "#overheadpress", "#shoulders"],
        "barbell_row": ["#barbellrow", "#backday", "#rows", "#pullday"],
        "pull_up": ["#pullups", "#calisthenics", "#bodyweight", "#backday"],
        "hip_thrust": ["#hipthrust", "#glutegains", "#glutesday", "#bootyworkout"],
        "leg_press": ["#legpress", "#legday", "#quads", "#legs"],
        "front_squat": ["#frontsquat", "#legday", "#quads", "#squats"],
        "bicep_curl": ["#biceps", "#armday", "#curls", "#arms"],
        "incline_bench": ["#inclinebench", "#upperchest", "#chestday", "#bench"],
        "romanian_deadlift": ["#rdl", "#romaniandeadlift", "#hamstrings", "#legday"],
        "lat_pulldown": ["#latpulldown", "#backday", "#lats", "#pullday"],
        "dumbbell_press": ["#dumbbellpress", "#chestday", "#dumbbells", "#chest"],
        "tricep_pushdown": ["#triceps", "#armday", "#pushday", "#arms"],
    },
    "branded": ["#prpath", "#prpathapp"],
    "body_map": ["#gains", "#musclegrowth", "#transformation", "#progresspic", "#beforeafter"],
    "nutrition": ["#mealprep", "#highprotein", "#healthyeating", "#macros", "#nutrition"],
    "relatable": ["#gymhumor", "#gymmemes", "#relatable", "#gymlife", "#gymproblems"],
}


def _pick_hashtags(exercise_key: str, content_type: str, count: int = 4) -> list[str]:
    """Pick a balanced mix of hashtags for a given exercise and content type."""
    tags = []

    # 1 broad
    tags.append(random.choice(HASHTAGS["broad_fitness"]))

    # 1-2 niche strength
    tags.append(random.choice(HASHTAGS["niche_strength"]))

    # 1 exercise-specific (if score_rank)
    if content_type == "score_rank" and exercise_key in HASHTAGS["exercise_specific"]:
        tags.append(random.choice(HASHTAGS["exercise_specific"][exercise_key]))
    elif content_type == "body_map":
        tags.append(random.choice(HASHTAGS["body_map"]))
    elif content_type == "nutrition":
        tags.append(random.choice(HASHTAGS["nutrition"]))
    elif content_type == "relatable":
        tags.append(random.choice(HASHTAGS["relatable"]))

    # 1 branded
    tags.append("#prpath")

    return tags[:count + 1]


# ─── Caption Generators Per Platform ────────────────────────────────

def _tiktok_caption(exercise_name: str, exercise_key: str, content_type: str, genome_id: str, bodyweight: str = "180") -> str:
    """
    TikTok: 200+ chars, keyword-rich, comment bait hook, 3-5 hashtags.
    TikTok's search indexes caption text — keywords matter more than hashtags.
    """
    hooks = {
        "score_rank": [
            f"What's your {exercise_name} strength score? Most people are stuck at Novice level without even knowing it. Swipe to see where you rank from Beginner to World Class based on your bodyweight. Comment your {exercise_name.lower()} weight below 👇",
            f"Are you Elite on {exercise_name}? Only 10% of lifters reach Elite level. Swipe through to find your tier based on a {bodyweight} lb bodyweight. Drop your max in the comments 💪",
            f"Where do you rank on {exercise_name}? Beginner, Novice, Intermediate, Advanced, Elite, or World Class? Swipe to find out based on your bodyweight. Comment your number 👇",
            f"Most gym bros think they're Advanced on {exercise_name} but they're actually Novice 😤 Swipe to see the real strength standards based on bodyweight. Where do you land?",
            f"Can you hit World Class on {exercise_name}? Only 1% of lifters make it. Swipe to check where your numbers actually put you. Be honest in the comments 👇",
        ],
        "body_map": [
            "6 months of consistency and this is what happens to your muscle development. Swipe to see the before and after body map. Tag someone who needs to see this 💪",
            "This is what progressive overload actually looks like on your body. Track every muscle group and watch the map light up. The glow up is real 🔥",
        ],
        "relatable": [
            "Tell me this isn't you on leg day 😤 Save this if you've been there",
        ],
        "nutrition": [
            "These food swaps changed my cut completely. High protein, lower calories, actually tastes good. Save this for your next grocery run 🍗",
        ],
    }

    hook = random.choice(hooks.get(content_type, hooks["score_rank"]))
    tags = _pick_hashtags(exercise_key, content_type, count=4)
    tag_str = " ".join(tags)

    return f"{hook}\n\n{tag_str} #gid_{genome_id}"


def _youtube_caption(exercise_name: str, exercise_key: str, content_type: str, genome_id: str, bodyweight: str = "180") -> dict:
    """
    YouTube Shorts: Keyword-first title (60 chars visible on mobile),
    detailed description with 3-5 hashtags. #Shorts required.
    Hashtags go in DESCRIPTION not title. First 3 hashtags appear above title.
    """
    titles = {
        "score_rank": [
            f"What's Your {exercise_name} Strength Score?",
            f"Are You Elite on {exercise_name}?",
            f"{exercise_name} Rank: Beginner to World Class",
            f"Where Do You Rank on {exercise_name}?",
        ],
        "body_map": [
            "6 Months of Gym: Before vs After Body Map",
            "Watch Your Muscles Light Up With Consistency",
        ],
    }

    descriptions = {
        "score_rank": [
            f"Find out where you rank on {exercise_name} from Beginner to World Class based on your bodyweight. These strength standards show exactly where your numbers put you compared to other lifters. Track your score free with PRPath on the App Store.",
            f"Most lifters think they're Advanced on {exercise_name} but the real standards tell a different story. Swipe through each tier to see where you actually land at {bodyweight} lbs bodyweight. PRPath tracks your strength score automatically.",
        ],
        "body_map": [
            "Watch what happens to your muscle development map after 6 months of consistent training. Every muscle group tracked, every session counted. See your own progress with PRPath.",
        ],
    }

    title = random.choice(titles.get(content_type, titles["score_rank"]))
    desc = random.choice(descriptions.get(content_type, descriptions["score_rank"]))
    tags = _pick_hashtags(exercise_key, content_type, count=4)
    tag_str = " ".join(tags)

    # #Shorts MUST be in description for Shorts shelf
    full_desc = f"{desc}\n\n#Shorts {tag_str} #gid_{genome_id}"

    return {"title": title, "description": full_desc}


def _instagram_caption(exercise_name: str, exercise_key: str, content_type: str, genome_id: str, bodyweight: str = "180") -> str:
    """
    Instagram: Keywords > hashtags in 2026. First 125 chars = visible hook.
    3-5 niche hashtags at END of caption. Broad hashtags = zero value.
    Algorithm reads caption keywords for discovery.
    """
    # First 125 chars must hook — this is the visible preview
    hooks = {
        "score_rank": [
            f"What's your {exercise_name} strength score? Most lifters don't know where they rank 👇",
            f"Are you actually Elite on {exercise_name}? Only 10% of lifters make it to this tier 💪",
            f"Beginner to World Class — where does your {exercise_name} put you? Swipe to find out",
        ],
        "body_map": [
            "This is what 6 months of consistent training looks like on a muscle map 🔥",
            "Your muscle development before vs after progressive overload. The glow up is real",
        ],
    }

    bodies = {
        "score_rank": [
            f"\n\nSwipe through each strength tier to see where your {exercise_name.lower()} numbers land based on a {bodyweight} lb bodyweight. From Beginner (just starting out) to World Class (top 1% of all lifters). These standards are based on real data.\n\nTrack your actual strength score with PRPath — free on the App Store.",
            f"\n\nThese are the real {exercise_name.lower()} strength standards based on bodyweight ratio. Most people are surprised where they actually land. Check each tier and be honest — where are you?\n\nPRPath calculates your score automatically from your workout logs.",
        ],
        "body_map": [
            "\n\nEvery muscle group tracked. Every session counted. Watch your body map go from gray to fully lit as you stay consistent.\n\nTrack your muscle development with PRPath — free on the App Store.",
        ],
    }

    hook = random.choice(hooks.get(content_type, hooks["score_rank"]))
    body = random.choice(bodies.get(content_type, bodies["score_rank"]))
    tags = _pick_hashtags(exercise_key, content_type, count=4)
    tag_str = " ".join(tags)

    return f"{hook}{body}\n\n{tag_str} #gid_{genome_id}"


def _facebook_caption(exercise_name: str, exercise_key: str, content_type: str, genome_id: str, bodyweight: str = "180") -> str:
    """
    Facebook: Keyword-rich, aligned with on-screen text. Shares = #1 signal.
    Keep it readable, add hashtags for categorization.
    85% watch muted — on-screen text handles the content.
    """
    captions = {
        "score_rank": [
            f"What's your {exercise_name} strength score? Swipe to see where you rank from Beginner to World Class based on your bodyweight 💪 Comment your number below\n\n#gym #strengthscore #fitness #{exercise_name.lower().replace(' ', '')} #prpath",
            f"Most people think they're Advanced on {exercise_name} but the real standards say otherwise. Check each tier and be honest 👇\n\n#gym #fitness #strengthtraining #{exercise_name.lower().replace(' ', '')} #prpath",
        ],
        "body_map": [
            "6 months of consistency. Watch the muscle map light up 🔥 Tag someone who needs to start tracking their gains\n\n#gym #fitness #gains #musclegrowth #transformation #prpath",
        ],
    }

    return random.choice(captions.get(content_type, captions["score_rank"])) + f" #gid_{genome_id}"


# ─── Main Generator ─────────────────────────────────────────────────

def generate_captions(
    exercise_key: str,
    content_type: str,
    genome_id: str,
    bodyweight: str = "180",
) -> dict:
    """
    Generate platform-optimized captions for a genome.

    Returns:
        {
            "tiktok": "full caption string",
            "youtube": {"title": "...", "description": "..."},
            "instagram": "full caption string",
            "facebook": "full caption string",
        }
    """
    # Get exercise display name
    try:
        data = _load_exercise_data()
        exercise_name = data["exercises"][exercise_key]["display_name"]
    except (KeyError, FileNotFoundError):
        exercise_name = exercise_key.replace("_", " ").title()

    return {
        "tiktok": _tiktok_caption(exercise_name, exercise_key, content_type, genome_id, bodyweight),
        "youtube": _youtube_caption(exercise_name, exercise_key, content_type, genome_id, bodyweight),
        "instagram": _instagram_caption(exercise_name, exercise_key, content_type, genome_id, bodyweight),
        "facebook": _facebook_caption(exercise_name, exercise_key, content_type, genome_id, bodyweight),
    }


# ─── CLI Test ───────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("PRPath GENESIS Caption Generator — Sample Output")
    print("=" * 60)

    captions = generate_captions("squat", "score_rank", "gen000_01_squat_180", "180")

    print("\n📱 TIKTOK:")
    print(f"   Length: {len(captions['tiktok'])} chars")
    print(f"   {captions['tiktok']}")

    print("\n📺 YOUTUBE:")
    print(f"   Title: {captions['youtube']['title']} ({len(captions['youtube']['title'])} chars)")
    print(f"   Description: {captions['youtube']['description']}")

    print("\n📸 INSTAGRAM:")
    print(f"   Length: {len(captions['instagram'])} chars")
    print(f"   First 125: {captions['instagram'][:125]}...")
    print(f"   Full: {captions['instagram']}")

    print("\n📘 FACEBOOK:")
    print(f"   {captions['facebook']}")

    print("\n" + "=" * 60)
    print("Body Map example:")
    print("=" * 60)
    captions2 = generate_captions("squat", "body_map", "gen000_05_bodymap", "180")
    print(f"\n📱 TIKTOK: {captions2['tiktok'][:150]}...")
    print(f"\n📺 YOUTUBE: {captions2['youtube']['title']}")
