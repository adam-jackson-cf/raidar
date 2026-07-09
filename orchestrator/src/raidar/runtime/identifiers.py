"""Identifier formatting shared across runtime surfaces."""

import re


def slug_fragment(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug:
        raise ValueError(f"Cannot build slug fragment from invalid identifier: {value!r}")
    return slug
