from typing import Any, Literal

from pydantic import BaseModel

class ArtifactManifestEntry(BaseModel):
    source: str
    destination: str
    type: Literal["file", "directory"]
    status: Literal["ok", "failed", "empty", "skipped"]
    service: str | None = ...
    exclude: list[str] | None = ...

class ArtifactManifest(BaseModel):
    entries: list[ArtifactManifestEntry] = ...
    def to_json_data(self) -> list[dict[str, Any]]: ...
