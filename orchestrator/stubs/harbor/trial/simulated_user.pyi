from pathlib import Path

from _typeshed import Incomplete
from harbor.utils.templating import create_prompt_environment as create_prompt_environment

REQUIRED_TEMPLATE_VARIABLES: Incomplete
OPTIONAL_TEMPLATE_VARIABLES: Incomplete
ALLOWED_TEMPLATE_VARIABLES = REQUIRED_TEMPLATE_VARIABLES | OPTIONAL_TEMPLATE_VARIABLES
DEFAULT_USER_PERSONA: str
DEFAULT_USER_PROMPT_TEMPLATE: str

def validate_user_agent_version_pin(
    target_name: str | None,
    target_version: str | None,
    user_agent_name: str | None,
    user_agent_version: str | None,
) -> None: ...
def validate_user_prompt_template(
    template_content: str, *, source: str, persona_path: Path | None = None
) -> None: ...
def load_user_prompt_template(
    template_path: Path | None, *, persona_path: Path | None = None
) -> str: ...
def load_user_persona(persona_path: Path | None) -> str: ...
def render_user_prompt(
    instruction: str,
    bridge_instructions: str,
    template_path: Path | None = None,
    persona_path: Path | None = None,
) -> str: ...
