"""Settings for the enrichment steps.

Same pattern as src/config.py: every setting has a default, can be overridden
by an environment variable (also read from .env) and by a command-line option.
Priority: command line > environment / .env > default.
"""
import argparse
import os
from dataclasses import dataclass, field
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

# USD per million tokens (prompts up to 100K tokens). Other models need
# LLM_PRICE_INPUT_PER_MTOK and LLM_PRICE_OUTPUT_PER_MTOK.
PRICES_PER_MTOK = {
    "claude-haiku-5-5": (0.10, 0.50),
}


@dataclass
class EnrichConfig:
    input_dir: str = "output"          # crawler output: products_XXXX.json
    data_dir: str = "data"             # sample, labels and results
    sample_size: int = 1000
    seed: int = 42
    max_input_chars: int = 2000        # descriptions are cut to this length
    short_description_chars: int = 50  # shorter descriptions get a rule flag
    llm_provider: str = "anthropic"
    llm_model: str = "claude-haiku-5-5"
    llm_api_key: Optional[str] = field(default=None, repr=False)  # never printed
    max_cost_usd: float = 2.0          # the run stops when spend reaches this
    workers: int = 4                   # parallel products
    timeout: float = 60.0              # seconds per LLM request
    price_input_per_mtok: Optional[float] = None
    price_output_per_mtok: Optional[float] = None
    limit: Optional[int] = None        # test run on the first N sampled products
    restart: bool = False              # ignore saved progress (the cache is kept)

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

    @property
    def run_dir(self):
        """Where enrich.py writes: a separate folder for --limit test runs."""
        return os.path.join(self.results_dir, "test") if self.limit else self.results_dir

    @property
    def cache_dir(self):
        return os.path.join(self.data_dir, "llm_cache")

    def prices(self):
        """(input, output) USD per million tokens for the configured model."""
        if self.price_input_per_mtok is not None and self.price_output_per_mtok is not None:
            return self.price_input_per_mtok, self.price_output_per_mtok
        if self.llm_model in PRICES_PER_MTOK:
            return PRICES_PER_MTOK[self.llm_model]
        raise SystemExit(f"No price known for {self.llm_model}: set LLM_PRICE_INPUT_PER_MTOK "
                         "and LLM_PRICE_OUTPUT_PER_MTOK")


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
    p.add_argument("--provider", dest="llm_provider",
                   default=_env("LLM_PROVIDER", str, d.llm_provider),
                   help="LLM provider (default: %(default)s)")
    p.add_argument("--model", dest="llm_model", default=_env("LLM_MODEL", str, d.llm_model),
                   help="model name (default: %(default)s)")
    p.add_argument("--max-cost", dest="max_cost_usd", type=float,
                   default=_env("LLM_MAX_COST_USD", float, d.max_cost_usd),
                   help="stop when the LLM spend reaches this many USD (default: %(default)s)")
    p.add_argument("--workers", type=int, default=_env("ENRICH_WORKERS", int, d.workers),
                   help="products processed in parallel (default: %(default)s)")
    p.add_argument("--timeout", type=float, default=_env("LLM_TIMEOUT", float, d.timeout),
                   help="seconds per LLM request (default: %(default)s)")
    p.add_argument("--limit", type=int, default=None,
                   help="test run on the first N sampled products, written to data/results/test/")
    p.add_argument("--restart", action="store_true",
                   help="ignore saved progress and process every product again (uses the cache)")
    args = p.parse_args(argv)
    config = EnrichConfig(**vars(args))
    config.llm_api_key = _env("LLM_API_KEY", str, None)  # only from .env or the environment
    config.price_input_per_mtok = _env("LLM_PRICE_INPUT_PER_MTOK", float, None)
    config.price_output_per_mtok = _env("LLM_PRICE_OUTPUT_PER_MTOK", float, None)
    if config.sample_size < 1 or config.max_input_chars < 1 or config.workers < 1:
        p.error("--sample-size, --max-input-chars and --workers must be at least 1")
    return config
