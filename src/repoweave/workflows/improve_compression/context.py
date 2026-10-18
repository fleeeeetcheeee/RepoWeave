from dataclasses import dataclass
from typing import Any

from repoweave.config import Settings
from repoweave.experiments.runner import Runner
from repoweave.observability.tracing import Tracer
from repoweave.storage.database import Store


@dataclass
class Context:
    store: Store
    settings: Settings
    runner: Runner
    tracer: Tracer
    agents: Any
