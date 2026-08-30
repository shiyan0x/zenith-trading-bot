"""Compatibility shim for old research imports.

All indicator implementations live in :mod:`src.indicators`; keeping this
thin module prevents duplicated formulas from silently drifting apart.
"""

from src.indicators import *  # noqa: F401,F403
