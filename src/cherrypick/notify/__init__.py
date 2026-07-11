"""Cherrypick notification layer.

The logging floor is the guarantee: every notification is written as a structured NOTIFY line
before any push channel is attempted, and a push-channel failure can never suppress that floor.
This is the minimum form of the walk-away promise — "notified, or at least warned through logging."
"""

from .notifier import Notifier, notify

__all__ = ["Notifier", "notify"]
