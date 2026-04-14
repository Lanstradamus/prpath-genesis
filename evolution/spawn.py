"""
GENESIS population spawner.

Given a scored parent generation, produces the next generation's population:
  - Exploit (50%): crossover children of top 25% + low mutation
  - Explore (25%): crossover + high mutation
  - Wildcards (17%): totally random genomes
  - Controls (8%): clones of hall-of-fame #1

Diversity rules:
  - No species occupies > 40% of population
  - Each species gets at least 1 slot if registered
  - Blacklisted genes are skipped during mutation

Usage:
  python -X utf8 spawn.py --from-gen 0 --to-gen 1 --size 15
"""
import argparse
import json
import random
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from mutator import Mutator

EVOLUTION_ROOT = Path(__file__).parent
GENS_DIR = EVOLUTION_ROOT / "generations"
HOF_PATH = EVOLUTION_ROOT / "hall_of_fame.json"
TEMPLATES_DIR = EVOLUTION_ROOT / "templates"

# Template slug → template name (PRPath only has one species: carousels)
TEMPLATE_SLUG_MAP = {
    "prpath_carousel": "PRPathCarousel",
}
COMPOSITION_TO_SLUG = {v: k for k, v in TEMPLATE_SLUG_MAP.items()}


def load_population(generation: int) -> dict:
    path = GENS_DIR / f"gen_{generation:03d}" / "population.json"
    if not path.exists():
        sys.exit(f"[spawn] Population not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_hall_of_fame() -> dict:
    if not HOF_PATH.exists():
        return {"entries": []}
    return json.loads(HOF_PATH.read_text(encoding="utf-8"))


def species_of(genome: dict) -> str:
    return genome["template"]


def get_top_parents(population: list[dict], top_fraction: float = 0.25) -> list[dict]:
    """Return the top N% of the population by fitness.composite_score.
    Assumes population is already scored (has `fitness.composite_score`).
    """
    scored = [g for g in population if g.get("fitness") and g["fitness"].get("composite_score") is not None]
    if not scored:
        # Unscored (e.g. Gen 0) — everyone is a valid parent
        return list(population)
    scored.sort(key=lambda g: g["fitness"]["composite_score"], reverse=True)
    n = max(1, int(len(scored) * top_fraction))
    return scored[:n]


def assign_pool_counts(total_size: int) -> dict:
    """Distribute total slots into pools. 50/25/17/8 rounding-safe."""
    exploit = round(total_size * 0.50)
    explore = round(total_size * 0.25)
    wildcards = round(total_size * 0.17)
    controls = total_size - exploit - explore - wildcards
    return {"exploit": exploit, "explore": explore, "wildcards": max(0, wildcards), "controls": max(0, controls)}


def enforce_diversity(counts_by_species: dict, max_fraction: float = 0.40, total: int = 24) -> bool:
    """Return True if no single species exceeds max_fraction of total."""
    cap = int(total * max_fraction)
    for species, n in counts_by_species.items():
        if n > cap:
            return False
    return True


def spawn_next_generation(
    prev_gen: int,
    next_gen: int,
    target_size: int = 15,
    rng_seed: Optional[int] = None,
) -> dict:
    """Core spawner: reads previous gen + HoF, produces next gen population."""
    rng = random.Random(rng_seed)

    prev_data = load_population(prev_gen)
    prev_pop = prev_data["population"]
    hof = load_hall_of_fame()

    # Group parents by species
    species_groups: dict[str, list[dict]] = {}
    for g in prev_pop:
        species_groups.setdefault(g["template"], []).append(g)

    # Top parents per species
    top_parents_by_species = {
        sp: get_top_parents(pop, 0.34)  # a bit generous at small pop sizes
        for sp, pop in species_groups.items()
    }

    pool_counts = assign_pool_counts(target_size)
    new_population: list[dict] = []
    slot_idx = 0

    def next_slot_id(species: str, pool: str, tag: str = "") -> str:
        nonlocal slot_idx
        slot_idx += 1
        short = species.lower().replace("video", "").replace("tool", "").replace("recap", "")[:6]
        suffix = f"_{tag}" if tag else ""
        return f"{short}_gen{next_gen:03d}_{slot_idx:02d}_{pool}{suffix}"

    def mutator_for(species: str) -> Mutator:
        slug = COMPOSITION_TO_SLUG.get(species)
        if not slug:
            raise ValueError(f"Unknown species: {species}")
        return Mutator(slug)

    # Spread species across pools as evenly as possible
    species_list = list(species_groups.keys())
    if not species_list:
        sys.exit("[spawn] No species found in previous generation")

    def pick_species(i: int) -> str:
        return species_list[i % len(species_list)]

    # 1. EXPLOIT — crossover + 5% mutation
    for i in range(pool_counts["exploit"]):
        sp = pick_species(i)
        parents = top_parents_by_species.get(sp, [])
        if len(parents) >= 2:
            a, b = rng.sample(parents, 2)
        elif len(parents) == 1:
            a = b = parents[0]
        else:
            continue
        m = mutator_for(sp)
        child = m.crossover(a, b, mutation_rate=0.05)
        child["genome_id"] = next_slot_id(sp, "exploit")
        child["generation"] = next_gen
        child["pool"] = "exploit"
        child["fitness"] = None
        child["status"] = "planned"
        new_population.append(child)

    # 2. EXPLORE — crossover + 20% mutation
    for i in range(pool_counts["explore"]):
        sp = pick_species(i)
        parents = top_parents_by_species.get(sp, [])
        if len(parents) >= 2:
            a, b = rng.sample(parents, 2)
        elif len(parents) == 1:
            a = b = parents[0]
        else:
            continue
        m = mutator_for(sp)
        child = m.crossover(a, b, mutation_rate=0.20)
        child["genome_id"] = next_slot_id(sp, "explore")
        child["generation"] = next_gen
        child["pool"] = "explore"
        child["fitness"] = None
        child["status"] = "planned"
        new_population.append(child)

    # 3. WILDCARDS — fully random genomes
    for i in range(pool_counts["wildcards"]):
        sp = pick_species(i)
        m = mutator_for(sp)
        child = m.random_genome()
        child["genome_id"] = next_slot_id(sp, "wildcard")
        child["generation"] = next_gen
        child["pool"] = "wildcard"
        child["fitness"] = None
        child["status"] = "planned"
        new_population.append(child)

    # 4. CONTROLS — clones of hall of fame #1 (if any) else top parent
    hof_entries = hof.get("entries", [])
    for i in range(pool_counts["controls"]):
        if hof_entries:
            clone_source = dict(hof_entries[0])
        else:
            # Fall back to top parent of a random species
            sp = pick_species(i)
            parents = top_parents_by_species.get(sp, [])
            if not parents:
                continue
            clone_source = dict(parents[0])

        # Preserve structural + content genes exactly
        child = {
            "template": clone_source["template"],
            "parent_ids": [clone_source["genome_id"]],
            "lineage_type": "clone",
            "structural_genes": dict(clone_source.get("structural_genes", {})),
            "content_genes": dict(clone_source.get("content_genes", {})),
            "props_overrides": dict(clone_source.get("props_overrides", {})),
            "genome_id": next_slot_id(clone_source["template"], "control"),
            "generation": next_gen,
            "pool": "control",
            "fitness": None,
            "status": "planned",
        }
        new_population.append(child)

    # Diversity check
    counts_by_species: dict[str, int] = {}
    for g in new_population:
        counts_by_species[g["template"]] = counts_by_species.get(g["template"], 0) + 1
    if not enforce_diversity(counts_by_species, max_fraction=0.40, total=len(new_population)):
        print(
            f"[warn] Diversity violation — one species exceeds 40%: {counts_by_species}",
            file=sys.stderr,
        )

    return {
        "generation": next_gen,
        "spawned_at": str(date.today()),
        "spawn_mode": "evolution" if prev_gen >= 0 else "seed",
        "parent_generation": prev_gen,
        "pool_counts": pool_counts,
        "species_counts": counts_by_species,
        "population": new_population,
    }


def _cli():
    p = argparse.ArgumentParser(description="GENESIS population spawner")
    p.add_argument("--from-gen", type=int, required=True)
    p.add_argument("--to-gen", type=int, required=True)
    p.add_argument("--size", type=int, default=15)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    result = spawn_next_generation(
        args.from_gen, args.to_gen, target_size=args.size, rng_seed=args.seed
    )

    out_dir = GENS_DIR / f"gen_{args.to_gen:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "population.json"

    if args.dry_run:
        print(json.dumps(result, indent=2))
    else:
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"[ok] Wrote gen_{args.to_gen:03d}/population.json")
        print(f"     {len(result['population'])} genomes spawned")
        print(f"     Species: {result['species_counts']}")
        print(f"     Pools: {result['pool_counts']}")


if __name__ == "__main__":
    _cli()
