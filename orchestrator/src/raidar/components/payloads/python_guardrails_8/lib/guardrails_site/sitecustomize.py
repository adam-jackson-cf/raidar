"""Measure a test run's Python subprocesses too (python-guardrails@8 tests check).

The tests check puts this directory and the gate's site-packages on
``PYTHONPATH`` for the test run only. ``coverage``'s ``patch = subprocess``
sets ``COVERAGE_PROCESS_CONFIG``; a child Python started by a test (a CLI
under test, say) imports this module at start-up and starts coverage, as the
``.pth`` file coverage installs in its own site-packages would. The gate's
paths are moved to the end of ``sys.path`` so the project's packages win.
Outside a test run (no ``COVERAGE_PROCESS_*`` variable) it does nothing.
"""

import os
import sys

if os.environ.get("COVERAGE_PROCESS_START") or os.environ.get("COVERAGE_PROCESS_CONFIG"):
    _tools = [
        path for path in os.environ.get("GUARDRAILS_TOOL_PATHS", "").split(os.pathsep) if path
    ]
    if _tools:
        _real = {os.path.realpath(path) for path in _tools}
        sys.path[:] = [p for p in sys.path if os.path.realpath(p or ".") not in _real] + _tools
    try:
        import coverage
    except ImportError:
        pass
    else:
        coverage.process_startup()
