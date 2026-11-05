"""Locate the uv binary supplied by Raidar's locked dependency."""

import uv


def locked_uv() -> str:
    """Return uv's installed binary, preserving its not-found error for callers."""
    return uv.find_uv_bin()
