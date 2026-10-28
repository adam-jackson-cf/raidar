"""Raidar's trusted in-container payload: stdlib-only, run as root with the pack's ``python -I -S``.

``calls.py`` uploads this directory to a root-owned location and invokes ``main`` through a tiny
launcher; no module here imports Raidar, a package or anything outside the standard library.
"""
