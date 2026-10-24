"""``prompt`` built-in: prompt-wrapper.

Maps onto Harbor's instruction rendering: the template file becomes the
agent's ``prompt_template_path`` and Harbor renders ``{{ instruction }}`` into
it before every run. Only that one placeholder is allowed, so the static text
around it is exact evidence for the discovery probe.
"""

from __future__ import annotations

import re

from raidar.subject.agent.config import Harness
from raidar.subject.treatment.catalog._common import (
    BOTH_HARNESSES,
    COMMON_FINGERPRINT,
    missing_lines,
    read_file,
    require_file_count,
    require_with_keys,
    text_lines,
)
from raidar.subject.treatment.definition.contribution import Contribution
from raidar.subject.treatment.definition.evidence import StepEvidence
from raidar.subject.treatment.definition.manifest import (
    ComponentConfigError,
    ComponentKind,
    ComponentManifest,
    ComponentUseContext,
    DiscoveryProbe,
    ProbeOutcome,
)

PLACEHOLDER_RE = re.compile(r"\{\{\s*instruction\s*\}\}")
TEMPLATE_SYNTAX_RE = re.compile(r"\{\{|\{%|\{#")


class PromptWrapper:
    manifest = ComponentManifest(
        name="prompt-wrapper",
        version="1",
        kind=ComponentKind.PROMPT,
        description="Wrap each step instruction in a template with one {{ instruction }} slot",
        supports=BOTH_HARNESSES,
        fingerprint=(*COMMON_FINGERPRINT, "raidar/subject/treatment/catalog/prompt.py"),
        discovery_probe=DiscoveryProbe(
            id="prompt-wrapper-rendered",
            description="The template's static text is in the prompt the harness received",
            evidence=(
                "codex-cli: rollout user messages",
                "claude-code: session transcript (sessions/projects/**.jsonl)",
            ),
        ),
    )

    def template(self, use: ComponentUseContext) -> str:
        require_with_keys(use, allowed=())
        require_file_count(use, minimum=1, maximum=1)
        text = read_file(use, use.files[0]).decode("utf-8")
        if len(PLACEHOLDER_RE.findall(text)) != 1:
            raise ComponentConfigError("template must contain exactly one {{ instruction }}")
        if TEMPLATE_SYNTAX_RE.search(PLACEHOLDER_RE.sub("", text)):
            raise ComponentConfigError("template may use no Jinja syntax besides {{ instruction }}")
        if not text_lines(PLACEHOLDER_RE.sub("", text)):
            raise ComponentConfigError("template must add text around {{ instruction }}")
        return text

    def contribute(self, use: ComponentUseContext) -> Contribution:
        self.template(use)
        return Contribution(prompt_template=use.file_path(use.files[0]))

    def probe(
        self, use: ComponentUseContext, evidence: StepEvidence, step_index: int
    ) -> ProbeOutcome:
        del step_index
        prompt = (
            evidence.codex_text("user")
            if use.harness is Harness.CODEX_CLI
            else evidence.claude_transcript_text
        )
        missing = missing_lines(PLACEHOLDER_RE.sub("", self.template(use)), prompt)
        if not missing:
            return ProbeOutcome(True, "every wrapper line found in the harness prompt")
        return ProbeOutcome(False, f"wrapper lines not in the harness prompt: {missing[:3]}")


PROMPT_WRAPPER = PromptWrapper()
