"""Registry of available price sources."""
from __future__ import annotations

from .amadeus import AmadeusProvider
from .base import Provider, ProviderError
from .demo import DemoProvider
from .google_flights import GoogleFlightsProvider

REGISTRY: dict[str, type[Provider]] = {
    p.key: p for p in (AmadeusProvider, GoogleFlightsProvider, DemoProvider)
}


def create_provider(key: str, settings: dict[str, str]) -> Provider:
    if key not in REGISTRY:
        raise ProviderError(f"Unknown price source '{key}'.")
    return REGISTRY[key](settings)


__all__ = ["REGISTRY", "Provider", "ProviderError", "create_provider"]
