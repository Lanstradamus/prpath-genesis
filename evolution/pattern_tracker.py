"""
GENESIS Cross-Generation Pattern Memory.

Reads all memory.jsonl rows, groups by gene values, calculates win rates
and avg fitness, detects multi-week trends, and writes patterns.json.

Separates real patterns from one-week flukes:
  - "Expression won this week" = signal
  - "Expression has won 6 of 8 weeks" = certainty

Usage:
  python -X utf8 pattern_tracker.py update       # rebuild patterns.json from memory
  python -X utf8 pattern_tracker.py status        # show current patterns
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

EVOLUTION_ROOT = Path(__file__).parent
MEMORY_PATH = EVOLUTION_ROOT / "memory.jsonl"
PATTERNS_PATH = EVOLUTION_ROOT / "patterns.json"


def _read_memory() -> list[dict]:
    """Read all rows from memory.jsonl."""
    if not MEMORY_PATH.exists():
        return []
    rows = []
    with MEMORY_PATH.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def _detect_trend(fitness_by_gen: dict[int, list[float]]) -> str:
    """Detect trend from per-generation average fitness.

    Returns: 'rising', 'declining', or 'flat'.
    Needs 3+ generations of data.
    """
    if len(fitness_by_gen) < 3:
        return "insufficient_data"

    gens = sorted(fitness_by_gen.keys())
    avgs = [sum(fitness_by_gen[g]) / len(fitness_by_gen[g]) for g in gens]

    # Look at last 3 generations
    recent = avgs[-3:]
    if recent[0] < recent[1] < recent[2]:
        return "rising"
    elif recent[0] > recent[1] > recent[2]:
        return "declining"
    return "flat"


def _compute_gene_performance(rows: list[dict], gene_category: str, gene_name: str) -> dict:
    """Compute performance stats for a specific gene across all values."""
    value_stats: dict[str, dict] = defaultdict(lambda: {
        "appearances": 0,
        "wins": 0,
        "fitness_scores": [],
        "fitness_by_gen": defaultdict(list),
    })

    for r in rows:
        genes = r.get(gene_category, {}) or {}
        value = genes.get(gene_name)
        if value is None:
            continue

        value_str = str(value)
        fitness = (r.get("fitness") or {}).get("composite_score", 0)
        pct = (r.get("fitness") or {}).get("percentile_in_generation", 0.5)
        gen = r.get("generation", 0)

        value_stats[value_str]["appearances"] += 1
        value_stats[value_str]["fitness_scores"].append(fitness)
        value_stats[value_str]["fitness_by_gen"][gen].append(fitness)
        if pct >= 0.75:  # top quartile = "win"
            value_stats[value_str]["wins"] += 1

    result = {}
    for value_str, stats in value_stats.items():
        n = stats["appearances"]
        avg_fitness = sum(stats["fitness_scores"]) / n if n else 0
        win_rate = stats["wins"] / n if n else 0
        confidence = min(n / 10, 1.0)
        trend = _detect_trend(dict(stats["fitness_by_gen"]))

        result[value_str] = {
            "appearances": n,
            "wins": stats["wins"],
            "avg_fitness": round(avg_fitness, 4),
            "win_rate": round(win_rate, 4),
            "confidence": round(confidence, 2),
            "trend": trend,
        }

    return result


def _compute_combo_performance(rows: list[dict]) -> dict:
    """Track tool+game combo performance across generations."""
    combo_stats: dict[str, dict] = defaultdict(lambda: {
        "appearances": 0,
        "wins": 0,
        "fitness_scores": [],
        "fitness_by_gen": defaultdict(list),
    })

    for r in rows:
        structural = r.get("structural_genes", {}) or {}
        content = r.get("content_genes", {}) or {}
        tool = structural.get("tool")
        game = content.get("game_niche")

        if not tool or not game:
            continue

        # Simplify game_niche: "roblox_horror" -> "roblox"
        game_key = game.split("_")[0]
        combo_key = f"{tool}+{game_key}"

        fitness = (r.get("fitness") or {}).get("composite_score", 0)
        pct = (r.get("fitness") or {}).get("percentile_in_generation", 0.5)
        gen = r.get("generation", 0)

        combo_stats[combo_key]["appearances"] += 1
        combo_stats[combo_key]["fitness_scores"].append(fitness)
        combo_stats[combo_key]["fitness_by_gen"][gen].append(fitness)
        if pct >= 0.75:
            combo_stats[combo_key]["wins"] += 1

    result = {}
    for combo_key, stats in combo_stats.items():
        n = stats["appearances"]
        avg_fitness = sum(stats["fitness_scores"]) / n if n else 0
        confidence = min(n / 10, 1.0)
        trend = _detect_trend(dict(stats["fitness_by_gen"]))

        result[combo_key] = {
            "appearances": n,
            "wins": stats["wins"],
            "avg_fitness": round(avg_fitness, 4),
            "confidence": round(confidence, 2),
            "trend": trend,
        }

    return result


def update_patterns() -> dict:
    """Rebuild patterns.json from all memory.jsonl data."""
    rows = _read_memory()
    if not rows:
        patterns = {
            "updated": str(date.today()),
            "total_genomes_analyzed": 0,
            "generations_seen": 0,
            "tool_performance": {},
            "game_performance": {},
            "music_performance": {},
            "posting_time_performance": {},
            "gene_combos": {},
        }
        PATTERNS_PATH.write_text(json.dumps(patterns, indent=2), encoding="utf-8")
        return patterns

    gens_seen = len(set(r.get("generation", 0) for r in rows))

    patterns = {
        "updated": str(date.today()),
        "total_genomes_analyzed": len(rows),
        "generations_seen": gens_seen,
        "tool_performance": _compute_gene_performance(rows, "structural_genes", "tool"),
        "game_performance": _compute_gene_performance(rows, "content_genes", "game_niche"),
        "music_performance": _compute_gene_performance(rows, "structural_genes", "music_track"),
        "posting_time_performance": _compute_gene_performance(rows, "structural_genes", "posting_time"),
        "gene_combos": _compute_combo_performance(rows),
    }

    PATTERNS_PATH.write_text(json.dumps(patterns, indent=2), encoding="utf-8")
    return patterns


def show_status():
    """Print current pattern intelligence."""
    if not PATTERNS_PATH.exists():
        print("[info] No patterns.json yet. Run 'update' first.")
        return

    patterns = json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    print(f"GENESIS Pattern Memory — updated {patterns['updated']}")
    print(f"Total genomes: {patterns['total_genomes_analyzed']}, Generations: {patterns['generations_seen']}")

    for section_name, section_key in [
        ("Tool Performance", "tool_performance"),
        ("Game Performance", "game_performance"),
        ("Music Performance", "music_performance"),
        ("Posting Time", "posting_time_performance"),
    ]:
        section = patterns.get(section_key, {})
        if not section:
            continue
        print(f"\n{section_name}:")
        sorted_items = sorted(section.items(), key=lambda x: x[1]["avg_fitness"], reverse=True)
        for value, stats in sorted_items[:10]:
            bar = "█" * int(stats["avg_fitness"] * 20)
            trend_icon = {"rising": "↑", "declining": "↓", "flat": "→"}.get(stats["trend"], "?")
            conf = "●" if stats["confidence"] >= 0.8 else "◐" if stats["confidence"] >= 0.4 else "○"
            print(f"  {value:<25} {stats['avg_fitness']:.3f} {bar:<10} "
                  f"w={stats['win_rate']:.0%} n={stats['appearances']} {trend_icon} {conf}")

    combos = patterns.get("gene_combos", {})
    if combos:
        print(f"\nTop Gene Combos:")
        sorted_combos = sorted(combos.items(), key=lambda x: x[1]["avg_fitness"], reverse=True)
        for combo, stats in sorted_combos[:8]:
            trend_icon = {"rising": "↑", "declining": "↓", "flat": "→"}.get(stats["trend"], "?")
            print(f"  {combo:<30} {stats['avg_fitness']:.3f} n={stats['appearances']} {trend_icon}")


def _cli():
    p = argparse.ArgumentParser(description="GENESIS Pattern Tracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update", help="Rebuild patterns.json from memory")
    sub.add_parser("status", help="Show current patterns")
    args = p.parse_args()

    if args.cmd == "update":
        patterns = update_patterns()
        n = patterns["total_genomes_analyzed"]
        gens = patterns["generations_seen"]
        print(f"[ok] Patterns updated: {n} genomes across {gens} generation(s)")
        print(f"     Tools: {len(patterns['tool_performance'])} tracked")
        print(f"     Games: {len(patterns['game_performance'])} tracked")
        print(f"     Combos: {len(patterns['gene_combos'])} tracked")
        print(f"     Written: {PATTERNS_PATH}")
    elif args.cmd == "status":
        show_status()


if __name__ == "__main__":
    _cli()
