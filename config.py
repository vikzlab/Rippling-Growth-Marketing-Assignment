"""
config.py
==========
Single source of truth for model names and other configurable constants.

All steps import from here rather than hardcoding model strings. Override
via env vars in .env; the defaults below are the tested, working values.
"""

import os

# Cheap/fast model — used for all low-stakes, bounded classification tasks:
# clarifying questions, research planning, follow-up routing, eval grading.
CHEAP_MODEL = os.getenv("CLAUDE_CHEAP_MODEL", "claude-haiku-4-5-20251001")

# Frontier model — used only for the final synthesis step, where reasoning
# quality directly determines whether the deliverable is any good.
FRONTIER_MODEL = os.getenv("CLAUDE_FRONTIER_MODEL", "claude-sonnet-4-6")
