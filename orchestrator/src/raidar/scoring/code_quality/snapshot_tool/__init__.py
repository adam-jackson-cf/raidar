"""Snapshot measurement program run inside the pinned ``scb-python-quality`` tool env.

These modules are ports of the SCB fork's Python producers (tree-sitter
symbols and call graph, import tracing, radon file rows, and the evaluator
interpreter's token/AST/clone semantics). They import only the standard
library and the tool env's pinned packages (tree-sitter, networkx, radon),
never Raidar, because they execute under the tool interpreter as
``python -I -c <bootstrap> <this package's directory> <file_io.py> <command>``
(see ``raidar.scoring.code_quality.measurement``). They read files only through
the ``SourceReader`` the entry is given (``raidar.contract.file_io``, loaded from
its file by the bootstrap). Raidar's host side reads their JSON output; nothing
here decides a score.
"""
