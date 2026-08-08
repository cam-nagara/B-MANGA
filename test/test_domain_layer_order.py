from __future__ import annotations

from bmanga_core.domain_ids import UIDKind, derived_uid
from bmanga_core.domain_model import DomainNode, PageDocument
import importlib.util
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_domain_layer_order_test"


def _load_module():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules.setdefault(PACKAGE, package)
    core = importlib.import_module("bmanga_core")
    sys.modules.setdefault(f"{PACKAGE}.bmanga_core", core)
    for module_name in ("domain_ids", "domain_model"):
        module = importlib.import_module(f"bmanga_core.{module_name}")
        sys.modules.setdefault(f"{PACKAGE}.bmanga_core.{module_name}", module)
    io_name = f"{PACKAGE}.io"
    io_package = types.ModuleType(io_name)
    io_package.__path__ = [str(ROOT / "io")]
    sys.modules.setdefault(io_name, io_package)
    ids_name = f"{io_name}.domain_projection_ids"
    ids_spec = importlib.util.spec_from_file_location(
        ids_name,
        ROOT / "io" / "domain_projection_ids.py",
    )
    assert ids_spec is not None and ids_spec.loader is not None
    ids_module = importlib.util.module_from_spec(ids_spec)
    sys.modules[ids_name] = ids_module
    ids_spec.loader.exec_module(ids_module)
    name = f"{io_name}.domain_layer_order"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "io" / "domain_layer_order.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ORDER = _load_module()
PROJECT_UID = "project_0123456789abcdef0123456789abcdef"
PAGE_UID = derived_uid(UIDKind.PAGE, PROJECT_UID, "layer-order")


def _uid(label: str) -> str:
    return derived_uid(UIDKind.NODE, PAGE_UID, label)


def _document() -> PageDocument:
    root = _uid("root")
    coma = _uid("coma")
    folder = _uid("folder")
    front = _uid("front")
    unknown = _uid("unknown")
    back = _uid("back")
    nodes = {
        root: DomainNode(root, "page", "p0001"),
        coma: DomainNode(coma, "coma", "c01"),
        folder: DomainNode(folder, "folder", "folder1"),
        front: DomainNode(front, "text", "text1"),
        unknown: DomainNode(unknown, "future", "future1"),
        back: DomainNode(back, "raster", "raster1"),
    }
    return PageDocument(
        PROJECT_UID,
        PAGE_UID,
        0,
        root,
        {},
        nodes,
        {
            root: [coma],
            coma: [folder],
            folder: [back, unknown, front],
            front: [],
            unknown: [],
            back: [],
        },
    )


def test_ranked_order_is_front_to_back_per_parent():
    document = _document()
    front = _uid("front")
    back = _uid("back")
    folder = _uid("folder")
    result = ORDER.apply_ranked_order(document, [front, back])
    assert result.children[folder] == [front, _uid("unknown"), back]
    assert document.children[folder] == [back, _uid("unknown"), front]


def test_ranked_order_ignores_duplicates_root_and_unknown_uids():
    document = _document()
    front = _uid("front")
    result = ORDER.apply_ranked_order(
        document,
        [document.root_uid, front, front, _uid("missing")],
    )
    assert result.children == document.children


def test_ranked_order_never_moves_nodes_between_parents():
    document = _document()
    coma = _uid("coma")
    front = _uid("front")
    result = ORDER.apply_ranked_order(document, [front, coma])
    assert result.children[document.root_uid] == [coma]
    assert result.children[_uid("folder")][-1] == front
