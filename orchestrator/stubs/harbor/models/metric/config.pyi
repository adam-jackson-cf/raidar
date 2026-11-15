from typing import Any

from harbor.models.metric.type import MetricType as MetricType
from pydantic import BaseModel

class MetricConfig(BaseModel):
    type: MetricType = ...
    kwargs: dict[str, Any] = ...
