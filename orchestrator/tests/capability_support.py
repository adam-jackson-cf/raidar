"""A complete capability declaration (all nine names) for admission and pooling fixtures."""

from __future__ import annotations

CAPABILITIES = {
    "permissions": "bypass",
    "network": "allowlist",
    "workflow_skills": "skills directory",
    "global_instructions": "home instructions file",
    "host_isolation": "throwaway home",
    "isolation_proof": "full",
    "command_output": "stream",
    "structured_output": "json reply",
    "version_pin": "1.0",
}
OTHER_HARNESS = {**CAPABILITIES, "permissions": "settings file", "host_isolation": "config dir"}
