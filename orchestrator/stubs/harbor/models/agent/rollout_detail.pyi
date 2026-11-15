from typing import Any, TypedDict

class RolloutDetail(TypedDict, total=False):
    prompt_token_ids: list[list[int]]
    completion_token_ids: list[list[int]]
    logprobs: list[list[float]]
    extra: dict[str, list[Any]]
