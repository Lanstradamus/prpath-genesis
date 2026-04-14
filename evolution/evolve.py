"""
GENESIS orchestrator — the Sunday ritual.

Full weekly evolution cycle:
  1. Load the previous generation's population + fresh metrics
  2. Score fitness for every genome → populate composite_score
  3. Append to memory.jsonl (append-only history)
  4. Update hall_of_fame.json (top 10 ever)
  5. Update blacklist.json (genes underperforming for 3+ consecutive gens)
  6. Spawn next generation via spawn.py
  7. Print summary

Usage:
  python -X utf8 evolve.py run --from-gen 0 --to-gen 1 --metrics metrics.json
  python -X utf8 evolve.py status
  python -X utf8 evolve.py score-only --gen 0 --metrics metrics.json
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fitness import score_genome, score_genome_per_platform
from spawn import spawn_next_generation, load_population

EVOLUTION_ROOT = Path(__file__).parent

# PRPath-specific: no search attribution yet (no keyword bank)
update_keyword_search_data = None

MEMORY_PATH = EVOLUTION_ROOT / "memory.jsonl"
HOF_PATH = EVOLUTION_ROOT / "hall_of_fame.json"
BLACKLIST_PATH = EVOLUTION_ROOT / "blacklist.json"
GENS_DIR = EVOLUTION_ROOT / "generations"

# Gold Lock for carousel content (different thresholds than video)
GOLD_LOCK_VIEWS = 50_000       # 50K+ views in 7 days
GOLD_LOCK_SAVE_RATE = 0.01     # 1% save rate (saves per view)


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def score_generation(generation: int, metrics_by_id: dict) -> list[dict]:
    """Attach fitness to every genome in a generation from a metrics dict.

    metrics_by_id: {"genome_id": {"views_7d": ..., "engagement_rate": ..., ...}}
    Returns the updated population list with fitness filled in.
    """
    pop_data = load_population(generation)
    updated = []
    for g in pop_data["population"]:
        gid = g["genome_id"]
        metrics = metrics_by_id.get(gid, {})
        fitness = score_genome(metrics)
        fitness["raw_metrics"] = metrics
        g["fitness"] = fitness
        g["status"] = "measured"
        updated.append(g)

    # Percentile rank (within this generation)
    scored = sorted(
        updated, key=lambda x: x["fitness"]["composite_score"], reverse=True
    )
    n = len(scored)
    for i, g in enumerate(scored):
        g["fitness"]["percentile_in_generation"] = round((n - i) / n, 4)

    # Persist the updated population
    pop_data["population"] = updated
    pop_data["scored_at"] = datetime.utcnow().isoformat() + "Z"
    _write_json(
        GENS_DIR / f"gen_{generation:03d}" / "population.json", pop_data
    )
    return updated


def append_to_memory(scored_population: list[dict]):
    rows = []
    for g in scored_population:
        # Compute per-platform fitness if raw metrics have per_platform data
        raw_metrics = (g.get("fitness") or {}).get("raw_metrics", {})
        platform_fitness = None
        if raw_metrics.get("per_platform"):
            platform_fitness = score_genome_per_platform(raw_metrics)

        row = {
            "generation": g["generation"],
            "genome_id": g["genome_id"],
            "template": g["template"],
            "pool": g.get("pool"),
            "parent_ids": g.get("parent_ids", []),
            "lineage_type": g.get("lineage_type"),
            "fitness": g.get("fitness"),
            "structural_genes": g.get("structural_genes"),
            "content_genes": g.get("content_genes"),
            "measured_at": datetime.utcnow().isoformat() + "Z",
        }
        if platform_fitness:
            row["platform_fitness"] = platform_fitness
        rows.append(row)
    _append_jsonl(MEMORY_PATH, rows)
    return len(rows)


def update_hall_of_fame(scored_population: list[dict]) -> dict:
    hof = _load_json(HOF_PATH, {"updated": str(date.today()), "max_size": 10, "entries": []})

    # Promote any gold-lock candidates first
    # Carousel criteria: 50K+ views AND 1%+ save rate (saves/views)
    for g in scored_population:
        metrics = (g.get("fitness") or {}).get("raw_metrics", {}) or {}
        views = metrics.get("views_7d", 0) or 0
        saves = metrics.get("saves", 0) or 0
        save_rate = saves / views if views > 0 else 0
        if views >= GOLD_LOCK_VIEWS and save_rate >= GOLD_LOCK_SAVE_RATE:
            g["status"] = "immortal"

    # Combine existing HoF with new candidates, rerank, keep top N
    combined = list(hof.get("entries", [])) + scored_population
    # Dedupe by genome_id (keep first occurrence = highest scored if we sort first)
    seen: set[str] = set()
    deduped = []
    for entry in combined:
        gid = entry.get("genome_id")
        if gid and gid not in seen:
            seen.add(gid)
            deduped.append(entry)
    deduped.sort(
        key=lambda x: (x.get("fitness") or {}).get("composite_score", 0), reverse=True
    )
    hof["entries"] = deduped[: hof.get("max_size", 10)]
    hof["updated"] = str(date.today())
    _write_json(HOF_PATH, hof)
    return hof


def update_blacklist(scored_population: list[dict]) -> dict:
    """Track gene values landing in the bottom 25% for 3+ consecutive gens.

    Simple implementation: loads all memory.jsonl, groups by (template, gene, value),
    counts how many consecutive recent generations each was bottom-quartile.
    """
    blacklist = _load_json(
        BLACKLIST_PATH,
        {
            "updated": str(date.today()),
            "rules": {
                "kill_after_exposures": 3,
                "kill_after_bottom_percentile": 0.25,
                "kill_after_consecutive_gens": 3,
            },
            "banned": {},
        },
    )

    if not MEMORY_PATH.exists():
        return blacklist

    # Read full history
    rows = []
    with MEMORY_PATH.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    # For each (template, gene_category, gene_name, value) track consecutive bottom gens
    from collections import defaultdict
    exposures = defaultdict(list)  # key -> list of (gen, is_bottom)
    for r in rows:
        tmpl = r.get("template")
        pct = (r.get("fitness") or {}).get("percentile_in_generation", 1.0)
        is_bottom = pct <= 0.25
        gen = r.get("generation")
        for category in ("structural_genes", "content_genes"):
            genes = r.get(category) or {}
            for gene_name, value in genes.items():
                # Only enum-ish (hashable, not dict/list)
                try:
                    key = (tmpl, gene_name, str(value))
                    exposures[key].append((gen, is_bottom))
                except TypeError:
                    continue

    banned = blacklist.get("banned", {})
    for (tmpl, gene_name, value_str), history in exposures.items():
        # Sort by generation
        history.sort(key=lambda x: x[0])
        # Check if last 3 consecutive generations were bottom
        if len(history) >= 3:
            last_three = history[-3:]
            if all(h[1] for h in last_three):
                slug_key = _composition_to_slug(tmpl)
                banned.setdefault(slug_key, {}).setdefault(gene_name, [])
                if value_str not in banned[slug_key][gene_name]:
                    banned[slug_key][gene_name].append(value_str)

    blacklist["banned"] = banned
    blacklist["updated"] = str(date.today())
    _write_json(BLACKLIST_PATH, blacklist)
    return blacklist


def update_search_attribution(scored_population: list[dict], metrics_by_id: dict):
    """Update keyword_bank.json with search % data from TikTok impression sources."""
    if update_keyword_search_data is None:
        return 0
    count = 0
    for g in scored_population:
        gid = g["genome_id"]
        metrics = metrics_by_id.get(gid, {})
        search_pct = metrics.get("search_pct", 0)
        # Try to find which keyword this genome used from its content_overrides
        overrides = g.get("content_overrides", {}) or {}
        keyword = overrides.get("target_keyword")
        if keyword and search_pct > 0:
            update_keyword_search_data(keyword, search_pct)
            count += 1
    return count


def _composition_to_slug(template: str) -> str:
    """Map template names to their gene slug (used for blacklist lookup)."""
    mapping = {
        "PRPathCarousel": "prpath_carousel",
    }
    return mapping.get(template, template.lower())


def run_full_cycle(from_gen: int, to_gen: int, metrics_path: Path, size: int = 15):
    """End-to-end Sunday ritual."""
    print(f"[evolve] Starting cycle: gen {from_gen} → gen {to_gen}")

    # 1. Load metrics
    metrics_data = json.loads(metrics_path.read_text(encoding="utf-8"))
    # Accept either {"metrics_by_id": {...}} or flat dict
    metrics_by_id = metrics_data.get("metrics_by_id", metrics_data)

    # 2. Score previous generation
    scored = score_generation(from_gen, metrics_by_id)
    print(f"[evolve] Scored {len(scored)} genomes from gen {from_gen}")

    # Print top 3 and bottom 3
    ranked = sorted(scored, key=lambda g: g["fitness"]["composite_score"], reverse=True)
    print("\n  Top 3:")
    for g in ranked[:3]:
        score = g["fitness"]["composite_score"]
        print(f"    {g['genome_id']:<45} {score:.4f}")
    print("  Bottom 3:")
    for g in ranked[-3:]:
        score = g["fitness"]["composite_score"]
        print(f"    {g['genome_id']:<45} {score:.4f}")

    # 2a. Update search attribution in keyword bank
    n_search = update_search_attribution(scored, metrics_by_id)
    if n_search:
        print(f"[evolve] Updated search attribution for {n_search} keyword(s)")

    # 2b. Per-platform fitness summary
    for g in ranked[:3]:
        raw = (g.get("fitness") or {}).get("raw_metrics", {})
        if raw.get("per_platform"):
            pf = score_genome_per_platform(raw)
            best = pf.get("best_platform", "?")
            worst = pf.get("worst_platform", "?")
            print(f"    {g['genome_id'][:30]} best={best} worst={worst}")

    # 3. Append to memory
    n_memory = append_to_memory(scored)
    print(f"\n[evolve] Appended {n_memory} rows to memory.jsonl")

    # 4. Update hall of fame
    hof = update_hall_of_fame(scored)
    print(f"[evolve] Hall of fame updated: {len(hof['entries'])} entries")
    immortal = [g for g in scored if g.get("status") == "immortal"]
    if immortal:
        print(f"  🏆 Gold-locked this cycle: {[g['genome_id'] for g in immortal]}")

    # 4b. Retention × scene correlation
    try:
        from retention_analyzer import aggregate_scene_retention
        retention_report = aggregate_scene_retention(metrics_by_id)
        if retention_report.get("genomes_analyzed", 0) > 0:
            print(f"[evolve] Retention analysis: {retention_report['genomes_analyzed']} genomes")
            print(f"  Worst scene: {retention_report.get('worst_scene', 'N/A')}")
            for rec in retention_report.get("recommendations", []):
                print(f"  ⚠️  {rec}")
    except Exception as e:
        print(f"[warn] Retention analysis skipped: {e}")

    # 5. Update blacklist
    bl = update_blacklist(scored)
    banned_count = sum(
        len(genes) for genes in bl.get("banned", {}).values() for _ in genes
    )
    if banned_count:
        print(f"[evolve] Blacklist now has {banned_count} banned gene values")

    # 6. Spawn next generation
    result = spawn_next_generation(from_gen, to_gen, target_size=size)
    out_path = GENS_DIR / f"gen_{to_gen:03d}" / "population.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(out_path, result)
    print(f"\n[evolve] Spawned gen {to_gen:03d}: {len(result['population'])} genomes")
    print(f"  Species: {result['species_counts']}")
    print(f"  Pools:   {result['pool_counts']}")
    print(f"  Written: {out_path}")

    print("\n[evolve] Cycle complete. ✅")


def show_status():
    """Print a quick status of the evolution system."""
    print("=" * 60)
    print("GENESIS Evolution Engine — Status")
    print("=" * 60)

    # List generations
    gens = sorted(GENS_DIR.glob("gen_*"))
    print(f"\nGenerations: {len(gens)}")
    for g in gens:
        pop_file = g / "population.json"
        if pop_file.exists():
            data = json.loads(pop_file.read_text(encoding="utf-8"))
            n = len(data.get("population", []))
            mode = data.get("spawn_mode", "?")
            print(f"  {g.name}: {n} genomes ({mode})")

    # Hall of fame
    hof = _load_json(HOF_PATH, {"entries": []})
    print(f"\nHall of Fame: {len(hof.get('entries', []))} / {hof.get('max_size', 10)}")
    for i, e in enumerate(hof.get("entries", [])[:5], 1):
        score = (e.get("fitness") or {}).get("composite_score", 0)
        print(f"  {i}. {e.get('genome_id', '?'):<45} {score:.4f}")

    # Blacklist
    bl = _load_json(BLACKLIST_PATH, {"banned": {}})
    banned_total = sum(len(g) for s in bl.get("banned", {}).values() for g in s.values())
    print(f"\nBlacklist: {banned_total} banned gene values")

    # Memory
    mem_count = 0
    if MEMORY_PATH.exists():
        mem_count = sum(1 for _ in MEMORY_PATH.open(encoding="utf-8"))
    print(f"\nMemory: {mem_count} total genome records")

    print("=" * 60)


def _cli():
    p = argparse.ArgumentParser(description="GENESIS orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run full evolution cycle")
    p_run.add_argument("--from-gen", type=int, required=True)
    p_run.add_argument("--to-gen", type=int, required=True)
    p_run.add_argument("--metrics", type=Path, required=True, help="JSON file with metrics_by_id")
    p_run.add_argument("--size", type=int, default=15)

    sub.add_parser("status", help="Show current system status")

    p_score = sub.add_parser("score-only", help="Score a generation without spawning next")
    p_score.add_argument("--gen", type=int, required=True)
    p_score.add_argument("--metrics", type=Path, required=True)

    args = p.parse_args()

    if args.cmd == "run":
        run_full_cycle(args.from_gen, args.to_gen, args.metrics, args.size)
    elif args.cmd == "status":
        show_status()
    elif args.cmd == "score-only":
        metrics_data = json.loads(args.metrics.read_text(encoding="utf-8"))
        metrics_by_id = metrics_data.get("metrics_by_id", metrics_data)
        scored = score_generation(args.gen, metrics_by_id)
        ranked = sorted(scored, key=lambda g: g["fitness"]["composite_score"], reverse=True)
        for g in ranked:
            score = g["fitness"]["composite_score"]
            print(f"  {g['genome_id']:<45} {score:.4f}")


if __name__ == "__main__":
    _cli()