"""B-MANGAのPhase横断・決定的認定ランナー。"""

from .manifest import load_manifest
from .summary import build_summary

__all__ = ("build_summary", "load_manifest")
