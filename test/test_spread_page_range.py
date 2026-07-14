from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_spread_page_range_test"
_WORK_HOLDER = {"value": None}


def _module(name: str, **values):
    module = ModuleType(name)
    for key, value in values.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _load_page_range():
    package = _module(PACKAGE)
    package.__path__ = [str(ROOT)]
    io_package = _module(f"{PACKAGE}.io")
    io_package.__path__ = [str(ROOT / "io")]
    utils_package = _module(f"{PACKAGE}.utils")
    utils_package.__path__ = [str(ROOT / "utils")]
    core_package = _module(f"{PACKAGE}.core")
    core_package.__path__ = [str(ROOT / "core")]
    operators_package = _module(f"{PACKAGE}.operators")
    operators_package.__path__ = [str(ROOT / "operators")]

    page_io = _module(f"{PACKAGE}.io.page_io")
    work_io = _module(f"{PACKAGE}.io.work_io")
    io_package.page_io = page_io
    io_package.work_io = work_io
    layer_objects = _module(
        f"{PACKAGE}.utils.layer_object_model",
        iter_layer_objects=lambda _kind: (),
        parent_key=lambda _obj: "",
        content_layer=lambda _obj: None,
    )
    _module(f"{PACKAGE}.utils.gp_object_layer")
    _module(f"{PACKAGE}.utils.layer_stack")
    _module(f"{PACKAGE}.utils.page_grid")
    _module(f"{PACKAGE}.utils.log", get_logger=logging.getLogger)
    _module(
        f"{PACKAGE}.utils.layer_hierarchy",
        page_stack_key=lambda page: str(getattr(page, "id", "")),
        split_child_key=lambda value: (str(value), ""),
    )
    utils_package.layer_object_model = layer_objects
    _module(
        f"{PACKAGE}.core.work",
        get_work=lambda _context: _WORK_HOLDER["value"],
    )
    _module(
        f"{PACKAGE}.core.mode",
        MODE_PAGE="PAGE",
        get_mode=lambda _context: "PAGE",
    )
    _module(
        f"{PACKAGE}.operators.coma_op",
        create_basic_frame_coma=lambda *_args, **_kwargs: None,
    )

    name = f"{PACKAGE}.utils.page_range"
    spec = importlib.util.spec_from_file_location(name, ROOT / "utils" / "page_range.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, page_io


PAGE_RANGE, PAGE_IO = _load_page_range()


def _page(page_id: str, *, spread: bool = False, in_range: bool = True):
    return SimpleNamespace(id=page_id, spread=spread, in_page_range=in_range)


def _work(pages, *, start: int, end: int, active: int = 0, work_dir: str = "unused"):
    return SimpleNamespace(
        pages=list(pages),
        work_info=SimpleNamespace(page_number_start=start, page_number_end=end),
        active_page_index=active,
        loaded=True,
        work_dir=work_dir,
    )


def test_spread_counts_as_two_numbered_page_slots():
    work = _work([_page("p0001-0002", spread=True), _page("p0003")], start=1, end=3)
    assert PAGE_RANGE.page_slot_count(work) == 3


def test_page_range_visibility_advances_two_slots_for_spread():
    spread = _page("p0001-0002", spread=True)
    following = _page("p0003")
    work = _work([spread, following], start=1, end=2, active=1)

    assert PAGE_RANGE.update_page_range_visibility(work) is True
    assert spread.in_page_range is True
    assert following.in_page_range is False
    assert work.active_page_index == 0


def test_reopen_does_not_create_phantom_page_for_two_page_spread(monkeypatch, tmp_path):
    work = _work(
        [_page("p0001-0002", spread=True)],
        start=1,
        end=2,
        work_dir=str(tmp_path),
    )
    _WORK_HOLDER["value"] = work
    created = []
    monkeypatch.setattr(
        PAGE_IO,
        "register_new_page",
        lambda _work: created.append(True),
        raising=False,
    )

    assert PAGE_RANGE.ensure_pages_for_number_range(SimpleNamespace(screen=None)) == 0
    assert created == []
    assert [page.id for page in work.pages] == ["p0001-0002"]
