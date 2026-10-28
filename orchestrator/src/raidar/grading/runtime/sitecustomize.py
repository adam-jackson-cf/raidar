"""Expose the declared-packages closure like a site-packages directory.

``PYTHONPATH`` alone ignores ``.pth`` files, so a ``--target`` closure would lose
packages that rely on them (namespace-package ``*-nspkg.pth``, path hooks).
``site.addsitedir`` processes them. This runs offline, as the sandbox user, from
the root-owned runtime directory; installing the closure ran no ``.pth`` code.
"""

import os
import site

for _entry in filter(None, os.environ.get("RAIDAR_SUBMISSION_DEPS", "").split(os.pathsep)):
    site.addsitedir(_entry)
