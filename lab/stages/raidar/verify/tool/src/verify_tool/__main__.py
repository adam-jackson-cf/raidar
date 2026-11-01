"""Run a check: ``python -m verify_tool lint|format|types`` (the ``verify`` command)."""

from .checks import main

raise SystemExit(main())
