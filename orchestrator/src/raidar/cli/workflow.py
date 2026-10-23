"""Register the experiment workflow commands in lifecycle order.

Author and lock an experiment (``init``, ``validate``, ``lock``), provide its problems
and runtime pack (``problem``, ``pack``), run it (``plan``, ``run``,
``perf``), score it (``assess``, ``jev``) and analyse it (``analyze``).
"""

from __future__ import annotations

import click

from raidar.cli import (
    analyze,
    assess,
    execution,
    experiment_init,
    experiment_spec,
    jev,
    pack,
    perf,
    problem,
)


def register(main: click.Group) -> None:
    """Attach the experiment workflow commands to the public CLI group."""
    experiment_init.register(main)
    experiment_spec.register(main)
    problem.register(main)
    pack.register(main)
    execution.register(main)
    perf.register(main)
    assess.register(main)
    jev.register(main)
    analyze.register(main)
