from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import Enum
from uuid import UUID

from _typeshed import Incomplete
from harbor.environments.base import OutputStream as OutputStream
from harbor.models.job.lock import TrialLock as TrialLock
from harbor.models.trial.config import TrialConfig as TrialConfig
from harbor.models.trial.result import TrialResult as TrialResult
from pydantic import BaseModel

LogPhase: Incomplete

class TrialEvent(Enum):
    START = "start"
    ENVIRONMENT_START = "environment-start"
    AGENT_START = "agent-start"
    AGENT_END = "agent-end"
    VERIFICATION_START = "verification-start"
    END = "end"
    CANCEL = "cancel"

class TrialHookEvent(BaseModel):
    ...
    event: TrialEvent
    task_name: str
    config: TrialConfig
    result: TrialResult
    lock: TrialLock
    timestamp: datetime = ...
    @property
    def trial_name(self) -> str: ...
    @property
    def trial_id(self) -> UUID: ...

HookCallback = Callable[[TrialHookEvent], Awaitable[None]]

class LogEntry(BaseModel):
    trial_id: UUID
    phase: LogPhase
    stream: OutputStream
    text: str
    timestamp: datetime = ...
    step_name: str | None = ...

LogCallback = Callable[[LogEntry], Awaitable[None]]
