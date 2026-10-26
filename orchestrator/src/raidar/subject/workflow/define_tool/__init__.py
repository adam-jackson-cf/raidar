"""The define tool payload: what a feature definition says and how it is built and checked.

The modules are factory-x-loops' ``capability/define`` at the commit below, with the engine's
interpreter env vars and run directory replaced by Raidar's state directory. The package imports
only itself and the standard library, so a copy of this directory runs on its own
(``python -m define_tool``) on a pinned interpreter.
"""

SOURCE_REPOSITORY = "factory-x-loops"
SOURCE_COMMIT = "ac5db0f28364911ef2298e428748120be8b0d56c"
