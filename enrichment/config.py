"""Settings for the enrichment steps.

Same pattern as src/config.py: every setting has a default, can be overridden
by an environment variable (also read from .env) and by a command-line option.
Priority: command line > environment / .env > default.
"""
import argparse
import os
from dataclasses import dataclass
from typing import Optional

from src.config import load_dotenv

# Fixed top-level categories, so LLM answers can be compared and measured.
CATEGORIES = [
    "books",
    "phones_tablets",
    "computers_accessories",
    "electronics_audio_camera",
    "home_appliances",
    "home_living",
    "beauty_personal_care",
    "health_medical",
    "mother_baby",
    "toys_games",
    "fashion_women",
    "fashion_men",
    "shoes_bags_accessories",
    "sports_outdoor",
    "automotive_motorbike",
    "food_beverages",
    "stationery_office",
    "pets",
    "other",
]


@dataclass
class EnrichConfig:
    input_dir: str = "output"          # crawler output: products_XXXX.json
    data_dir: str = "data"             # sample, labels and results
    sample_size: int = 1000
    seed: int = 42
    max_input_chars: int = 2000        # descriptions are cut to this length
    short_description_chars: int = 50  # shorter descriptions get a rule flag

    @property
    def sample_path(self):
        return os.path.join(self.data_dir, "sample_products.jsonl")

    @property
    def manifest_path(self):
        return os.path.join(self.data_dir, "sample_manifest.json")

    @property
    def results_dir(self):
        return os.path.join(self.data_dir, "results")

    @property
    def rule_flags_path(self):
        return os.path.join(self.results_dir, "rule_flags.jsonl")


def _env(name, cast, default):
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return cast(value)


def parse_args(argv=None, description="Tiki enrichment step"):
    """Build an EnrichConfig from defaults, environment variables and options."""
    load_dotenv()
    d = EnrichConfig()
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--input-dir", default=_env("ENRICH_INPUT_DIR", str, d.input_dir),
                   help="folder with the crawler's products_XXXX.json (default: %(default)s)")
    p.add_argument("--data-dir", default=_env("ENRICH_DATA_DIR", str, d.data_dir),
                   help="folder for the sample and results (default: %(default)s)")
    p.add_argument("--sample-size", type=int,
                   default=_env("ENRICH_SAMPLE_SIZE", int, d.sample_size),
                   help="products in the sample (default: %(default)s)")
    p.add_argument("--seed", type=int, default=_env("ENRICH_SEED", int, d.seed),
                   help="random seed for the sample (default: %(default)s)")
    p.add_argument("--max-input-chars", type=int,
                   default=_env("LLM_MAX_INPUT_CHARS", int, d.max_input_chars),
                   help="cut descriptions to this many characters (default: %(default)s)")
    p.add_argument("--short-description-chars", type=int,
                   default=_env("ENRICH_SHORT_DESCRIPTION_CHARS", int,
                                d.short_description_chars),
                   help="flag descriptions shorter than this (default: %(default)s)")
    args = p.parse_args(argv)
    config = EnrichConfig(**vars(args))
    if config.sample_size < 1 or config.max_input_chars < 1:
        p.error("--sample-size and --max-input-chars must be at least 1")
    return config
