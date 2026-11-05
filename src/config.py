"""Crawler settings.

Every setting has a default, can be overridden by an environment variable
(TIKI_<NAME>, also read from a .env file) and by a command-line option.
Priority: command line > environment / .env > default.
"""
import argparse
import os
from dataclasses import dataclass
from typing import Optional

API_URL = "https://api.tiki.vn/product-detail/api/v1/products/{}"


@dataclass
class Config:
    input_file: str = "products-0-200000.csv"
    output_dir: str = "output"
    api_url: str = API_URL
    batch_size: int = 1000
    workers: int = 1               # parallel requests; 2 was slower because of blocks
    start_delay: float = 0.6       # seconds each worker waits before a request at the start
    min_delay: float = 0.45        # fastest allowed pace per worker
    max_delay: float = 10.0        # slowest pace per worker
    block_pause: int = 300         # first pause after a block, doubles on repeated blocks
    max_block_pause: int = 900     # never pause longer than 15 minutes
    speed_up_after: int = 50       # successes in a row before the delay gets shorter
    max_network_retries: int = 5   # attempts before a network error is recorded
    timeout: int = 15              # seconds per request
    transport: str = "auto"        # "pycurl", "curl" or "auto" (pycurl if installed)
    curl_path: Optional[str] = None  # curl binary for the "curl" transport; found on PATH if empty
    limit: Optional[int] = None    # crawl only the first N IDs (test mode)


def load_dotenv(path=".env"):
    """Read KEY=VALUE lines from a .env file into os.environ (existing values win)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name, cast, default):
    value = os.environ.get(f"TIKI_{name.upper()}")
    if value in (None, ""):
        return default
    return cast(value)


def parse_args(argv=None):
    """Build a Config from defaults, environment variables and command-line options."""
    load_dotenv()
    d = Config()
    p = argparse.ArgumentParser(
        description="Download Tiki product details into JSON files of 1,000 products.")
    p.add_argument("--input", dest="input_file", default=_env("input_file", str, d.input_file),
                   help="CSV file with an 'id' column (default: %(default)s)")
    p.add_argument("--output", dest="output_dir", default=_env("output_dir", str, d.output_dir),
                   help="folder for the JSON files (default: %(default)s)")
    p.add_argument("--batch-size", type=int, default=_env("batch_size", int, d.batch_size),
                   help="IDs per output file (default: %(default)s)")
    p.add_argument("--workers", type=int, default=_env("workers", int, d.workers),
                   help="parallel requests (default: %(default)s)")
    p.add_argument("--start-delay", type=float, default=_env("start_delay", float, d.start_delay),
                   help="seconds between requests at the start (default: %(default)s)")
    p.add_argument("--min-delay", type=float, default=_env("min_delay", float, d.min_delay),
                   help="shortest delay (default: %(default)s)")
    p.add_argument("--max-delay", type=float, default=_env("max_delay", float, d.max_delay),
                   help="longest delay (default: %(default)s)")
    p.add_argument("--block-pause", type=int, default=_env("block_pause", int, d.block_pause),
                   help="seconds to pause after the first block (default: %(default)s)")
    p.add_argument("--max-block-pause", type=int,
                   default=_env("max_block_pause", int, d.max_block_pause),
                   help="longest pause in seconds (default: %(default)s)")
    p.add_argument("--speed-up-after", type=int,
                   default=_env("speed_up_after", int, d.speed_up_after),
                   help="successes in a row before speeding up (default: %(default)s)")
    p.add_argument("--retries", dest="max_network_retries", type=int,
                   default=_env("max_network_retries", int, d.max_network_retries),
                   help="attempts per ID on network errors (default: %(default)s)")
    p.add_argument("--timeout", type=int, default=_env("timeout", int, d.timeout),
                   help="seconds per request (default: %(default)s)")
    p.add_argument("--transport", choices=["auto", "pycurl", "curl"],
                   default=_env("transport", str, d.transport),
                   help="HTTP transport (default: %(default)s)")
    p.add_argument("--curl-path", default=_env("curl_path", str, d.curl_path),
                   help="curl binary for --transport curl (default: curl on PATH)")
    p.add_argument("--limit", type=int, default=_env("limit", int, d.limit),
                   help="crawl only the first N IDs into output_test/ (test mode)")
    modes = p.add_mutually_exclusive_group()
    modes.add_argument("--retry-errors", dest="mode", action="store_const", const="retry",
                       help="request again the saved IDs that failed for a reason other "
                            "than HTTP 404, then exit")
    modes.add_argument("--reclean", dest="mode", action="store_const", const="reclean",
                       help="re-clean the descriptions of saved files and exit (Bug 9 fix)")
    p.set_defaults(mode="crawl")
    args = p.parse_args(argv)

    values = {k: v for k, v in vars(args).items() if k != "mode"}
    config = Config(**values)
    if config.limit and config.output_dir == d.output_dir:
        config.output_dir = "output_test"  # keep test runs away from the real output
    if config.workers < 1 or config.batch_size < 1:
        p.error("--workers and --batch-size must be at least 1")
    return config, args.mode
