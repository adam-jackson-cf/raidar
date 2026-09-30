"""Canonical production-quality detector and environment contract (SCB fork, verbatim).

Source: ``slop-code-bench`` ``src/slop_code/metrics/scoring/contract.py`` at
``25069ba``. V is measured with exactly this Ruff version and rule set, under
a CPython 3.12 evaluator interpreter; any other environment is
``measurement_environment_mismatch``. The pinned tools come from the Python
pack's tool lock (``scb-python-quality``); a test keeps the two in step.
"""

from __future__ import annotations

EXPECTED_RUFF_VERSION = "0.8.6"
EXPECTED_INTERPRETER_IMPLEMENTATION = "cpython"
EXPECTED_INTERPRETER_VERSION = "3.12"
EXPECTED_INTERPRETER_CACHE_TAG = "cpython-312"
EXPECTED_INTERPRETER = {
    "implementation": EXPECTED_INTERPRETER_IMPLEMENTATION,
    "version": EXPECTED_INTERPRETER_VERSION,
    "cache_tag": EXPECTED_INTERPRETER_CACHE_TAG,
}
RUFF_RULES = (
    "SIM102",
    "SIM103",
    "SIM108",
    "SIM109",
    "SIM110",
    "SIM111",
    "SIM114",
    "SIM116",
    "SIM401",
)
RUFF_ARGUMENTS = (
    "-m",
    "ruff",
    "check",
    "--isolated",
    "--output-format",
    "json",
    "--select",
    ",".join(RUFF_RULES),
)
PARSER_TOKENIZER_PROBE = b"x = 1\nif x:\n    y = 'z'\n"
FILE_BYTE_POLICY = {
    "input": "raw_file_bytes",
    "transport": "hex",
    "hash": "sha256",
    "path_identity": "lexical_utf8_relative",
}
PARSER_TOKENIZER_ALGORITHM = {
    "ast": {
        "annotate_fields": True,
        "include_attributes": False,
    },
    "clone": {
        "minimum_source_lines": 12,
        "minimum_statements": 6,
        "overlap": "same_path_inclusive_line_ranges_do_not_overlap",
        "selection": "maximal_statement_vectors",
    },
    "source_lines": {
        "excluded_token_names": (
            "COMMENT",
            "DEDENT",
            "ENCODING",
            "ENDMARKER",
            "INDENT",
            "NEWLINE",
            "NL",
        ),
        "range": "inclusive_token_lines_positive_only",
    },
}
PRODUCTION_QUALITY_CONTRACT = {
    "expected_interpreter": EXPECTED_INTERPRETER,
    "file_bytes": FILE_BYTE_POLICY,
    "parser_tokenizer": {
        "algorithm": PARSER_TOKENIZER_ALGORITHM,
        "probe_hex": PARSER_TOKENIZER_PROBE.hex(),
    },
    "ruff": {
        "arguments": RUFF_ARGUMENTS,
        "expected_version": EXPECTED_RUFF_VERSION,
        "rules": RUFF_RULES,
        "parser_diagnostics": "ignored_after_locked_cpython_acceptance",
    },
}
