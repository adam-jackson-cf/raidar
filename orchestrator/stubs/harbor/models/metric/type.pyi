from enum import StrEnum

class MetricType(StrEnum):
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    MEAN = "mean"
    UV_SCRIPT = "uv-script"
