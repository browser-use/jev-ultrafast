"""Browser backend implementations.

The package intentionally keeps the optional Ego dependency behind the backend
selection path. Importing :mod:`jev_ultrafast` therefore remains enough for the
upstream Browser Harness backend.
"""

from .base import BrowserBackend

__all__ = ["BrowserBackend"]
