"""Directory put on the tool interpreter's ``sys.path`` so ``snapshot_tool`` imports alone.

It holds nothing but the ``snapshot_tool`` package, so no Raidar module can
shadow an import inside the tool env.
"""
