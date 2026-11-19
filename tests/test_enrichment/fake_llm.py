"""A fake LLM provider for the tests: answers depend on words in the product name."""
import json

from enrichment.llm_client import TransientLLMError

ENRICH = {"category": "books", "brand": None, "key_attributes": [], "language": "vi",
          "confidence": 0.8}


class FakeProvider:
    """BAD -> invalid answers, BADONCE -> invalid first then valid, FAKE -> counterfeit flag,
    BOOM -> temporary API error, REFUSE -> refusal. Costs 1000 input + 100 output tokens."""

    name = "fake"

    def __init__(self):
        self.calls = []
        self.seen = set()

    def complete(self, system, user, json_schema, max_tokens):
        name = json.loads(user.split("\n\n")[0])["name"]
        step = "flag" if "flags" in json_schema["properties"] else "enrich"
        self.calls.append((name, step))
        if "BOOM" in name:
            raise TransientLLMError("server overloaded")
        if "REFUSE" in name:
            return "", "refusal", 1000, 0
        first_time = (name, step) not in self.seen
        self.seen.add((name, step))
        if "BAD" in name and ("BADONCE" not in name or first_time):
            return '{"category": "nonsense"}', "end_turn", 1000, 100
        if step == "enrich":
            return json.dumps(ENRICH), "end_turn", 1000, 100
        flags = []
        if "FAKE" in name:
            flags = [{"flag": "counterfeit_or_brand_risk", "severity": "high",
                      "evidence": f"the name says {name}"}]
        return json.dumps({"flags": flags}), "end_turn", 1000, 100
