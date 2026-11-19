"""Provider interface shared by all price sources."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Offer, Query


class ProviderError(Exception):
    """A search failed in a way the user should see (auth, rate limit, page layout...)."""


class Provider(ABC):
    key: str = ""
    label: str = ""

    def __init__(self, settings: dict[str, str]):
        self.settings = settings

    def missing_config(self) -> list[str]:
        """Names of settings required before this provider can run."""
        return []

    def pause_between_queries(self) -> float:
        return 0.0

    def close(self) -> None:
        """Release resources held for the duration of a run (browsers, sessions)."""

    @abstractmethod
    def search(self, query: Query) -> list[Offer]:
        ...
