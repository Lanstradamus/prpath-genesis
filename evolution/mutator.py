"""
GENESIS mutator — creates new gene values within a template's allowed gene space.

Supports:
  - enum: pick a different value from allowed set
  - float: sample within range with bias toward neighborhood of current value
  - int: same as float but discrete

Also respects blacklist.json — never proposes banned values.

Usage:
  from mutator import Mutator
  m = Mutator("title_optimizer")
  new_value = m.mutate_gene("hook_accent_color", current="#FF4444")
  new_genome = m.mutate_genome(parent_genome, mutation_rate=0.2)
  child = m.crossover(parent_a, parent_b)
"""
import json
import random
import sys
from pathlib import Path
from typing import Any, Optional

EVOLUTION_ROOT = Path(__file__).parent
TEMPLATES_DIR = EVOLUTION_ROOT / "templates"


class Mutator:
    def __init__(self, template_slug: str, blacklist_path: Optional[Path] = None):
        """template_slug: e.g. 'title_optimizer' (loads templates/title_optimizer.genes.json)"""
        self.slug = template_slug
        self.gene_def = self._load_gene_def(template_slug)
        self.blacklist = self._load_blacklist(blacklist_path or EVOLUTION_ROOT / "blacklist.json")
        self.rng = random.Random()

    def _load_gene_def(self, slug: str) -> dict:
        path = TEMPLATES_DIR / f"{slug}.genes.json"
        if not path.exists():
            sys.exit(f"[mutator] Gene definition not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_blacklist(self, path: Path) -> dict:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("banned", {}).get(self.slug, {})

    def _is_banned(self, gene_name: str, value: Any) -> bool:
        banned_values = self.blacklist.get(gene_name, [])
        return value in banned_values

    def _get_gene_spec(self, gene_name: str) -> Optional[dict]:
        """Find a gene spec in either structural_genes or content_genes."""
        for category in ("structural_genes", "content_genes"):
            if gene_name in self.gene_def.get(category, {}):
                return self.gene_def[category][gene_name]
        return None

    def _gene_category(self, gene_name: str) -> Optional[str]:
        for category in ("structural_genes", "content_genes"):
            if gene_name in self.gene_def.get(category, {}):
                return category
        return None

    def mutate_gene(self, gene_name: str, current: Any = None) -> Any:
        """Return a new value for `gene_name`, respecting constraints and blacklist."""
        spec = self._get_gene_spec(gene_name)
        if not spec:
            return current

        gene_type = spec.get("type", "enum")

        if gene_type == "enum":
            values = [v for v in spec["values"] if not self._is_banned(gene_name, v) and v != current]
            if not values:
                # Fall back to any non-banned value
                values = [v for v in spec["values"] if not self._is_banned(gene_name, v)]
            return self.rng.choice(values) if values else current

        elif gene_type == "float":
            lo, hi = spec["range"]
            if current is not None:
                # Neighborhood sample: ±30% of range, clipped
                span = (hi - lo) * 0.3
                new = current + self.rng.uniform(-span, span)
                return round(max(lo, min(hi, new)), 2)
            return round(self.rng.uniform(lo, hi), 2)

        elif gene_type == "int":
            lo, hi = spec["range"]
            if current is not None:
                span = max(1, int((hi - lo) * 0.3))
                new = current + self.rng.randint(-span, span)
                return max(lo, min(hi, new))
            return self.rng.randint(lo, hi)

        return current

    def random_gene(self, gene_name: str) -> Any:
        """Pick a completely random value for a gene (wildcard mode)."""
        return self.mutate_gene(gene_name, current=None)

    def mutate_genome(self, parent: dict, mutation_rate: float = 0.2) -> dict:
        """Return a child genome with some genes mutated.

        mutation_rate: probability each gene is mutated (0-1).
        """
        child = {
            "template": parent["template"],
            "parent_ids": [parent["genome_id"]],
            "lineage_type": "mutation",
            "structural_genes": dict(parent.get("structural_genes", {})),
            "content_genes": dict(parent.get("content_genes", {})),
        }

        for category in ("structural_genes", "content_genes"):
            for gene_name in list(child[category].keys()):
                if self.rng.random() < mutation_rate:
                    child[category][gene_name] = self.mutate_gene(
                        gene_name, current=child[category][gene_name]
                    )

        return child

    def crossover(self, parent_a: dict, parent_b: dict, mutation_rate: float = 0.05) -> dict:
        """Produce a child genome by randomly inheriting each gene from either parent.

        Then apply low-rate mutation on top.
        """
        child = {
            "template": parent_a["template"],
            "parent_ids": [parent_a["genome_id"], parent_b["genome_id"]],
            "lineage_type": "crossover",
            "structural_genes": {},
            "content_genes": {},
        }

        for category in ("structural_genes", "content_genes"):
            # Union of all gene names from both parents
            all_genes = set(parent_a.get(category, {}).keys()) | set(
                parent_b.get(category, {}).keys()
            )
            for gene_name in all_genes:
                # Coin flip — inherit from parent A or B
                source = parent_a if self.rng.random() < 0.5 else parent_b
                value = source.get(category, {}).get(gene_name)
                if value is None:
                    # Fallback to the other parent if one is missing this gene
                    other = parent_b if source is parent_a else parent_a
                    value = other.get(category, {}).get(gene_name)
                if value is not None:
                    child[category][gene_name] = value

        # Apply light mutation on top of crossover
        if mutation_rate > 0:
            for category in ("structural_genes", "content_genes"):
                for gene_name in list(child[category].keys()):
                    if self.rng.random() < mutation_rate:
                        child[category][gene_name] = self.mutate_gene(
                            gene_name, current=child[category][gene_name]
                        )

        return child

    def random_genome(self) -> dict:
        """Generate a completely random wildcard genome."""
        genome = {
            "template": self.gene_def["template"],
            "parent_ids": [],
            "lineage_type": "seed",
            "structural_genes": {},
            "content_genes": {},
        }
        for category in ("structural_genes", "content_genes"):
            for gene_name in self.gene_def.get(category, {}):
                genome[category][gene_name] = self.random_gene(gene_name)
        return genome


# ─── CLI ──────────────────────────────────────────────────────────────
def _cli():
    import argparse
    p = argparse.ArgumentParser(description="GENESIS gene mutator")
    p.add_argument("--template", required=True, help="Template slug (e.g. title_optimizer)")
    p.add_argument("--action", choices=["mutate-gene", "random-genome", "mutate-genome", "crossover"], required=True)
    p.add_argument("--gene-name", help="For mutate-gene")
    p.add_argument("--current", help="Current value for mutate-gene")
    p.add_argument("--parent-file", help="Path to parent genome JSON (for mutate-genome/crossover)")
    p.add_argument("--parent-file-b", help="Second parent for crossover")
    p.add_argument("--mutation-rate", type=float, default=0.2)
    args = p.parse_args()

    m = Mutator(args.template)

    if args.action == "mutate-gene":
        result = m.mutate_gene(args.gene_name, current=args.current)
        print(json.dumps({"gene_name": args.gene_name, "old": args.current, "new": result}, indent=2))

    elif args.action == "random-genome":
        genome = m.random_genome()
        print(json.dumps(genome, indent=2))

    elif args.action == "mutate-genome":
        parent = json.loads(Path(args.parent_file).read_text(encoding="utf-8"))
        child = m.mutate_genome(parent, mutation_rate=args.mutation_rate)
        print(json.dumps(child, indent=2))

    elif args.action == "crossover":
        parent_a = json.loads(Path(args.parent_file).read_text(encoding="utf-8"))
        parent_b = json.loads(Path(args.parent_file_b).read_text(encoding="utf-8"))
        child = m.crossover(parent_a, parent_b, mutation_rate=args.mutation_rate)
        print(json.dumps(child, indent=2))


if __name__ == "__main__":
    _cli()
