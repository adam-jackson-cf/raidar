"""Harbor runtime validation helpers."""

from __future__ import annotations

import re

DOCKER_COMPOSE_VERSION_PATTERN = re.compile(r"(?:^|[^0-9])v?(\d+)\.(\d+)\.(\d+)(?:[^0-9]|$)")
DOCKERFILE_FROM_PATTERN = re.compile(
    r"^\s*FROM(?:\s+--platform=[^\s]+)?\s+([^\s]+)",
    re.IGNORECASE | re.MULTILINE,
)
PUBLIC_REGISTRY_HOSTS: set[str] = {
    "docker.io",
    "index.docker.io",
    "registry-1.docker.io",
    "ghcr.io",
    "quay.io",
    "mcr.microsoft.com",
    "public.ecr.aws",
    "gcr.io",
    "us.gcr.io",
    "eu.gcr.io",
    "asia.gcr.io",
    "registry.k8s.io",
}


def parse_docker_compose_version(raw: str) -> tuple[int, int, int] | None:
    match = DOCKER_COMPOSE_VERSION_PATTERN.search(raw.strip())
    if not match:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def dockerfile_from_images(dockerfile_content: str) -> list[str]:
    return [match.group(1) for match in DOCKERFILE_FROM_PATTERN.finditer(dockerfile_content)]


def image_registry_host(image: str) -> str | None:
    first_segment = image.split("/", 1)[0].strip().lower()
    if not first_segment or first_segment == "scratch":
        return None
    if "/" not in image:
        return None
    if "." in first_segment or ":" in first_segment or first_segment == "localhost":
        return first_segment
    return None


def validate_public_base_images(dockerfile_content: str) -> None:
    for image in dockerfile_from_images(dockerfile_content):
        if image.startswith("$"):
            raise ValueError(
                f"Dockerfile FROM image must be explicit, found unresolved variable: {image}."
            )
        host = image_registry_host(image)
        if host and host not in PUBLIC_REGISTRY_HOSTS:
            raise ValueError(
                f"Dockerfile uses private or unsupported registry host '{host}' in FROM '{image}'. "
                "Only public registries are allowed."
            )
