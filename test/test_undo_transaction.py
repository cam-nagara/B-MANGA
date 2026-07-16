from array import array
import importlib.util
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "utils" / "undo_transaction.py"
_SPEC = importlib.util.spec_from_file_location("bmanga_undo_transaction", _MODULE_PATH)
undo_transaction = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(undo_transaction)


def test_nested_state_detects_micro_change() -> None:
    before = {"rect": (10.0, 20.0, 30.0, 40.0), "enabled": True}
    after = {"rect": (10.01, 20.0, 30.0, 40.0), "enabled": True}
    assert undo_transaction.states_differ(before, after)


def test_rounding_noise_does_not_create_empty_step() -> None:
    before = [1.0, {"position": (2.0, 3.0)}]
    after = [1.0 + 1.0e-9, {"position": (2.0, 3.0 - 1.0e-9)}]
    assert not undo_transaction.states_differ(before, after)


def test_return_to_origin_is_not_a_change() -> None:
    start = (4.0, 8.0, 12.0, 16.0)
    final = (4.0, 8.0, 12.0, 16.0)
    assert not undo_transaction.states_differ(start, final)


def test_bool_is_not_treated_as_numeric_one() -> None:
    assert undo_transaction.states_differ(True, 1)


def test_array_pixel_snapshot_detects_change() -> None:
    before = array("f", [0.0, 0.25, 0.5, 1.0])
    after = array("f", [0.0, 0.25, 0.51, 1.0])
    assert undo_transaction.states_differ(before, after)
