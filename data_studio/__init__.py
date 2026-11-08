"""Data Studio: a local-first analytics workbench.

The public surface is intentionally small. Everything else is reached through the
kernel, project, api and cli subpackages.
"""

from __future__ import annotations

from .version import API_VERSION, PROJECT_FORMAT_VERSION, __version__

__all__ = ["API_VERSION", "PROJECT_FORMAT_VERSION", "__version__"]
