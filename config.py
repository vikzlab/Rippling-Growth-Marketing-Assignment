"""
Centralized model configuration. Override via env vars.
"""

import os

CHEAP_MODEL = os.getenv("CLAUDE_CHEAP_MODEL", "claude-haiku-4-5-20251001")
FRONTIER_MODEL = os.getenv("CLAUDE_FRONTIER_MODEL", "claude-sonnet-4-6")
