"""Scenario implementations.

Each scenario lives in its own module. Importing this package
triggers handler registration via the @register_scenario decorator.

ScenarioEngine relies on this side-effect: when the package is imported
(e.g. at app startup), all handlers become available in the registry.
"""
from app.services.scenarios import echo

__all__ = ["echo"]
