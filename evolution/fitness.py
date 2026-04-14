"""
PRPath GENESIS fitness function — adapted for carousel content.

Different from LaunchLens video fitness because carousels don't have
"watch time" the same way. The 2026 algorithm weights saves and
comments much heavier than likes. Our weights:

  30% views + 25% saves + 20% comments + 15% shares + 5% likes + 5% profile_views

All components normalized to 0-1 before weighting.

Usage:
  from fitness import score_genome, score_population
  score = score_genome(genome_metrics_dict)
  ranked = score_population(population_list)
"""
import json
import math
import sys
from pathlib import Path
from typing import Optional

EVOLUTION_ROOT = Path(__file__).parent

# Weights — tuned for carousel content (saves + comments = algorithm signals in 2026)
W_VIEWS = 0.30
W_SAVES = 0.25          # "I want to remember this" = high intent
W_COMMENTS = 0.20       # "Where do I rank?" = algorithm fuel
W_SHARES = 0.15         # Sends/shares weighted heavy in 2026
W_LIKES = 0.05          # Vanity metric
W_PROFILE_VIEWS = 0.05  # Proxy for download intent

# Normalization caps (tune as data arrives)
VIEWS_LOG_CAP = math.log(50_000 + 1)     # 50K views = 1.0
SAVES_LOG_CAP = math.log(1_000 + 1)      # 1K saves = 1.0 (high bar)
COMMENTS_LOG_CAP = math.log(500 + 1)     # 500 comments = 1.0
SHARES_LOG_CAP = math.log(500 + 1)       # 500 shares = 1.0
LIKES_LOG_CAP = math.log(10_000 + 1)     # 10K likes = 1.0
PROFILE_VIEWS_CAP = 100                   # 100 profile visits = 1.0


def log_normalize(value: int, cap: float) -> float:
    if value <= 0:
        return 0.0
    return min(math.log(value + 1) / cap, 1.0)


def linear_normalize(value: int, cap: int) -> float:
    if value is None or value <= 0:
        return 0.0
    return min(value / cap, 1.0)


def score_genome(metrics: dict) -> dict:
    """Compute composite fitness from a carousel's metrics dict.

    Expected keys (all optional — missing = 0):
      views_7d, saves, comments, shares, likes, profile_views
    """
    views_score = log_normalize(metrics.get("views_7d", 0) or 0, VIEWS_LOG_CAP)
    saves_score = log_normalize(metrics.get("saves", 0) or 0, SAVES_LOG_CAP)
    comments_score = log_normalize(metrics.get("comments", 0) or 0, COMMENTS_LOG_CAP)
    shares_score = log_normalize(metrics.get("shares", 0) or 0, SHARES_LOG_CAP)
    likes_score = log_normalize(metrics.get("likes", 0) or 0, LIKES_LOG_CAP)
    profile_views_score = linear_normalize(metrics.get("profile_views", 0), PROFILE_VIEWS_CAP)

    composite = (
        W_VIEWS * views_score
        + W_SAVES * saves_score
        + W_COMMENTS * comments_score
        + W_SHARES * shares_score
        + W_LIKES * likes_score
        + W_PROFILE_VIEWS * profile_views_score
    )

    return {
        "composite_score": round(max(0.0, composite), 4),
        "components": {
            "views_score": round(views_score, 4),
            "saves_score": round(saves_score, 4),
            "comments_score": round(comments_score, 4),
            "shares_score": round(shares_score, 4),
            "likes_score": round(likes_score, 4),
            "profile_views_score": round(profile_views_score, 4),
        },
    }


def score_genome_per_platform(metrics: dict) -> dict:
    """Score fitness separately for each platform.

    Expects metrics['per_platform'] = {tiktok: {...}, youtube: {...}, ...}
    """
    platforms = {}
    per_platform = metrics.get("per_platform", {})

    for platform in ("tiktok", "tiktok_business", "youtube", "instagram", "facebook"):
        pm = per_platform.get(platform, {})
        if not pm:
            continue
        platform_metrics = {
            "views_7d": pm.get("views", 0),
            "saves": pm.get("saves", 0),
            "comments": pm.get("comments", 0),
            "shares": pm.get("shares", 0),
            "likes": pm.get("likes", 0),
            "profile_views": pm.get("profile_views", 0),
        }
        platforms[platform] = score_genome(platform_metrics)

    if not platforms:
        return {
            "composite_score": score_genome(metrics)["composite_score"],
            "platform_scores": {},
            "best_platform": None,
            "worst_platform": None,
        }

    return {
        "composite_score": score_genome(metrics)["composite_score"],
        "platform_scores": platforms,
        "best_platform": max(platforms, key=lambda p: platforms[p]["composite_score"]),
        "worst_platform": min(platforms, key=lambda p: platforms[p]["composite_score"]),
    }


def score_population(population: list[dict]) -> list[dict]:
    """Score every genome in a population and attach percentile rank.

    Input: list of {genome_id, metrics: {...}}
    Output: same list with `fitness` key added, sorted by composite_score desc.
    """
    results = []
    for item in population:
        metrics = item.get("metrics", {})
        fitness = score_genome(metrics)
        fitness["raw_metrics"] = metrics
        results.append({**item, "fitness": fitness})

    results.sort(key=lambda x: x["fitness"]["composite_score"], reverse=True)

    # Assign percentile ranks (1.0 = best)
    n = len(results)
    if n > 0:
        for i, r in enumerate(results):
            r["fitness"]["percentile_in_generation"] = round((n - i) / n, 4)

    return results


if __name__ == "__main__":
    # Test the fitness function
    test_metrics = {
        "views_7d": 50000,
        "saves": 500,
        "comments": 100,
        "shares": 50,
        "likes": 5000,
        "profile_views": 30,
    }
    result = score_genome(test_metrics)
    print("Test score:")
    print(json.dumps(result, indent=2))
