"""LLM enrichment and risk flagging of the crawled Tiki products.

Each step runs on its own with `python -m enrichment.<step>`, reads the file
written by the previous step and writes its own. See docs/llm_enrichment_spec.md.
"""
