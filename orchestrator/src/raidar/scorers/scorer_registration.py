"""Canonical scorer implementation registration."""

from __future__ import annotations

from importlib import import_module

for module in (
    "raidar.scorers.acceptance",
    "raidar.scorers.code_task",
    "raidar.scorers.resource_efficiency",
    "raidar.scorers.requirements",
    "raidar.scorers.design_to_code",
    "raidar.scorers.plan_to_code",
    "raidar.scorers.test_generation",
):
    import_module(module)
