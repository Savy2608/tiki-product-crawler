"""End-to-end tests for main.py with a fake transport: batches, errors, resume and config."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
from src import storage  # noqa: E402
from src.api_client import Throttle, TikiClient  # noqa: E402
from src.config import Config, parse_args  # noqa: E402


class FakeTiki:
    """Answers like Tiki: IDs ending in 0 do not exist, the others return a product."""

    name = "fake"

    def __init__(self):
        self.requested = []

    def get(self, url):
        pid = url.rsplit("/", 1)[1]
        self.requested.append(pid)
        if pid.endswith("0"):
            return 404, ""
        return 200, json.dumps({"id": int(pid), "name": f"Product {pid}", "url_key": f"p{pid}",
                                "price": 1000, "description": "<p>Nice</p>",
                                "images": [{"base_url": f"https://img/{pid}.jpg"}]})


class FakeTikiCase(unittest.TestCase):
    """A temporary output folder and a client that talks to FakeTiki."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Config(output_dir=self.tmp.name, batch_size=2, start_delay=0, min_delay=0)
        self.transport = FakeTiki()
        self.client = TikiClient(self.config, self.transport, Throttle(self.config, log=lambda m: None))
        self.ids = ["11", "20", "33", "44", "50"]

    def tearDown(self):
        self.tmp.cleanup()

    def run_crawl(self):
        with mock.patch.object(main, "log"):
            return main.crawl(self.config, self.client, self.ids)


class CrawlTest(FakeTikiCase):
    def test_full_crawl_writes_batches_and_counts_every_id(self):
        self.assertTrue(self.run_crawl())
        files = sorted(os.listdir(self.tmp.name))
        self.assertEqual(files, ["errors_0001.json", "errors_0002.json", "errors_0003.json",
                                 "products_0001.json", "products_0002.json", "products_0003.json"])
        first = storage.load_json(os.path.join(self.tmp.name, "products_0001.json"))
        self.assertEqual(first, [{"id": 11, "name": "Product 11", "url_key": "p11", "price": 1000,
                                  "description": "Nice", "images": ["https://img/11.jpg"]}])
        summary = storage.verify(self.tmp.name, self.ids)
        self.assertEqual((summary["saved_products"], summary["failed_ids"],
                          summary["missing_ids"], summary["duplicate_ids"]), (3, 2, 0, 0))

    def test_resume_skips_finished_batches_and_finished_ids(self):
        storage.save_json(os.path.join(self.tmp.name, "products_0001.json"), [{"id": 11}])
        storage.save_json(os.path.join(self.tmp.name, "errors_0001.json"),
                          [{"id": "20", "reason": "HTTP 404 - product not found"}])
        batch2 = storage.BatchFiles(self.tmp.name, 2)
        with batch2.open_partial() as handle:
            batch2.append_partial(handle, "33", {"id": 33}, None)

        self.assertTrue(self.run_crawl())
        self.assertEqual(self.transport.requested, ["44", "50"])  # only the unfinished IDs
        self.assertFalse(os.path.exists(batch2.partial))


class RetryErrorsTest(FakeTikiCase):
    """--retry-errors requests again only the IDs that failed for a reason other than 404."""

    def test_retry_replaces_network_errors_and_keeps_order(self):
        storage.save_json(os.path.join(self.tmp.name, "products_0001.json"), [])
        storage.save_json(os.path.join(self.tmp.name, "errors_0001.json"), [
            {"id": "11", "reason": "request failed: curl exited with code 6"},
            {"id": "20", "reason": "HTTP 404 - product not found"},
        ])
        storage.save_json(os.path.join(self.tmp.name, "products_0002.json"),
                          [{"id": 44, "name": "kept"}])
        storage.save_json(os.path.join(self.tmp.name, "errors_0002.json"),
                          [{"id": "33", "reason": "HTTP 500"}])

        with mock.patch.object(main, "log"):
            main.retry_errors(self.config, self.client, self.ids)

        self.assertEqual(self.transport.requested, ["11", "33"])  # 404s are not retried
        load = lambda name: storage.load_json(os.path.join(self.tmp.name, name))  # noqa: E731
        self.assertEqual([p["id"] for p in load("products_0001.json")], [11])
        self.assertEqual(load("errors_0001.json"),
                         [{"id": "20", "reason": "HTTP 404 - product not found"}])
        self.assertEqual([p["id"] for p in load("products_0002.json")], [33, 44])  # input order
        self.assertEqual(load("errors_0002.json"), [])
        summary = storage.verify(self.tmp.name, ["11", "20", "33", "44"])
        self.assertEqual((summary["missing_ids"], summary["duplicate_ids"]), (0, 0))


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("src.config.load_dotenv"):
            config, mode = parse_args([])
        self.assertEqual((config.workers, config.batch_size, config.transport, config.output_dir),
                         (1, 1000, "auto", "output"))
        self.assertEqual(mode, "crawl")

    def test_environment_and_command_line(self):
        env = {"TIKI_WORKERS": "3", "TIKI_START_DELAY": "1.5"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("src.config.load_dotenv"):
            config, _ = parse_args(["--workers", "2", "--transport", "curl"])
        self.assertEqual(config.workers, 2)        # command line wins
        self.assertEqual(config.start_delay, 1.5)  # from the environment
        self.assertEqual(config.transport, "curl")

    def test_limit_uses_test_output_folder(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("src.config.load_dotenv"):
            config, _ = parse_args(["--limit", "50"])
        self.assertEqual((config.limit, config.output_dir), (50, "output_test"))

    def test_mode_flags(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("src.config.load_dotenv"):
            self.assertEqual(parse_args(["--reclean"])[1], "reclean")
            self.assertEqual(parse_args(["--retry-errors"])[1], "retry")
            with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                parse_args(["--reclean", "--retry-errors"])  # only one mode at a time


if __name__ == "__main__":
    unittest.main()
