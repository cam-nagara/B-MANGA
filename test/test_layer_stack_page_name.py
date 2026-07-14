from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "panels" / "layer_stack_detail_ui.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "_bmanga_layer_stack_page_name_test",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_module_only_exports_page_layer_name():
    module = _load_module()
    functions = {
        name
        for name, value in inspect.getmembers(module, inspect.isfunction)
        if value.__module__ == module.__name__
    }
    assert functions == {"page_layer_name"}
    assert module.__all__ == ["page_layer_name"]


def test_page_layer_name_uses_work_order_and_start_number():
    module = _load_module()
    pages = [SimpleNamespace(id="p0100"), SimpleNamespace(id="p9999")]
    work = SimpleNamespace(
        pages=pages,
        work_info=SimpleNamespace(page_number_start=5),
    )

    assert module.page_layer_name(pages[0], work) == "ページ005"
    assert module.page_layer_name(SimpleNamespace(id="p9999"), work) == "ページ006"


def test_page_layer_name_has_stable_fallbacks():
    module = _load_module()

    assert module.page_layer_name(SimpleNamespace(id="page_42")) == "ページ042"
    assert module.page_layer_name(SimpleNamespace(id="cover")) == "cover"
    assert module.page_layer_name(SimpleNamespace(id="")) == "ページ000"
