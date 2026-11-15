"""Raidar's trusted call payload as the gate tool imports it inside the container.

The container installs ``raidar.subject.primitive.payload`` as the top-level ``payload``
package under the trusted root; these are the same modules.
"""

from raidar.subject.primitive.payload import supervise as supervise, walk as walk
