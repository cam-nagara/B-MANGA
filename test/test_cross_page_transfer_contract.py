from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_moved_links_only_preserve_groups_with_two_transferred_members():
    transaction = _load(
        "bmanga_cross_page_transaction_test",
        "utils/cross_page_transfer_transaction.py",
    )
    plan = SimpleNamespace(
        uid_map={
            "gp:gp_old": "gp:gp_new",
            "effect:effect_old": "effect:effect_new",
            "text:p0001:text_old": "text:p0002:text_new",
        },
        source_link_original={
            "gp:gp_old": "group_a",
            "effect:effect_old": "group_a",
            "text:p0001:text_old": "group_b",
            "balloon:p0001:balloon_unmoved": "group_b",
        },
    )
    assert transaction._moved_link_groups(plan) == [
        ["gp:gp_new", "effect:effect_new"]
    ]


def test_link_singleton_cleanup_keeps_only_real_groups():
    link_stage = _load(
        "bmanga_cross_page_link_stage_test",
        "utils/cross_page_link_stage.py",
    )
    assert link_stage._clean_singletons(
        {
            "gp:a": "one",
            "gp:b": "two",
            "effect:c": "two",
            "text:p0001:d": "three",
        }
    ) == {"gp:b": "two", "effect:c": "two"}


def test_protected_stage_tokens_are_not_removed_during_failed_rollback():
    transaction = _load(
        "bmanga_cross_page_transaction_token_test",
        "utils/cross_page_transfer_transaction.py",
    )
    plan = SimpleNamespace(
        staged_tokens={
            "gp": {"gp:a", "gp:b"},
            "effect": {"effect:c"},
            "link": {"link:d"},
            "asset": set(),
        }
    )
    assert transaction._tokens_without(plan, {"gp:a", "effect:c"}) == {
        "gp": {"gp:b"},
        "effect": set(),
        "link": {"link:d"},
        "asset": set(),
    }
