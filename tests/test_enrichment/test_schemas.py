"""Tests for enrichment.schemas: good, malformed and out-of-range LLM answers."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from enrichment.schemas import (ENRICH_JSON_SCHEMA, Enrichment, FlagResult,  # noqa: E402
                                validate)

GOOD = {"category": "books", "brand": None,
        "key_attributes": [{"name": "author", "value": "Mo Willems"}],
        "language": "en", "confidence": 0.9}


def with_(**changes):
    return json.dumps(dict(GOOD, **changes))


class EnrichmentSchemaTest(unittest.TestCase):
    def test_good_answer(self):
        data, error = validate(Enrichment, json.dumps(GOOD))
        self.assertIsNone(error)
        self.assertEqual(data, GOOD)

    def test_malformed_json(self):
        data, error = validate(Enrichment, '{"category": "books",')
        self.assertIsNone(data)
        self.assertIn("schema validation failed", error)

    def test_wrong_enum_and_ranges(self):
        for bad in (with_(category="gadgets"), with_(language="fr"), with_(confidence=1.5),
                    with_(key_attributes=[{"name": "a", "value": "b"}] * 6),
                    with_(extra_field=1)):
            data, error = validate(Enrichment, bad)
            self.assertIsNone(data, bad)
            self.assertTrue(error)

    def test_empty_brand_becomes_null(self):
        data, _ = validate(Enrichment, with_(brand="  "))
        self.assertIsNone(data["brand"])

    def test_json_schema_lists_every_field_as_required(self):
        self.assertEqual(sorted(ENRICH_JSON_SCHEMA["required"]),
                         sorted(Enrichment.model_fields))


class FlagSchemaTest(unittest.TestCase):
    def test_no_flags_is_valid(self):
        self.assertEqual(validate(FlagResult, '{"flags": []}'), ({"flags": []}, None))

    def test_flag_needs_known_type_severity_and_evidence(self):
        ok = {"flag": "spam_or_low_quality", "severity": "low", "evidence": "Zalo 0909"}
        self.assertIsNone(validate(FlagResult, json.dumps({"flags": [ok]}))[1])
        for bad in (dict(ok, flag="price_implausible"), dict(ok, severity="urgent"),
                    dict(ok, evidence="")):
            self.assertIsNone(validate(FlagResult, json.dumps({"flags": [bad]}))[0])

    def test_each_flag_type_only_once(self):
        ok = {"flag": "spam_or_low_quality", "severity": "low", "evidence": "x"}
        self.assertIsNone(validate(FlagResult, json.dumps({"flags": [ok, ok]}))[0])


if __name__ == "__main__":
    unittest.main()
