from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest


def _load_paths():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("bmanga_paths", root / "utils" / "paths.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_page_and_coma_ids():
    paths = _load_paths()
    assert paths.format_page_id(1) == "p0001"
    assert paths.format_spread_id(20, 21) == "p0020-0021"
    assert paths.validate_page_id("p9999") == "p9999"
    assert paths.format_coma_id(1) == "c01"
    assert paths.format_coma_id(99) == "c99"
    assert paths.validate_coma_id("c01") == "c01"
    assert not paths.is_valid_coma_id("c00")
    assert not paths.is_valid_coma_id("c100")


def test_invalid_ids_raise():
    paths = _load_paths()
    for value in (0, 10000):
        try:
            paths.format_page_id(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"format_page_id({value}) did not raise")
    for value in (0, 100):
        try:
            paths.format_coma_id(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"format_coma_id({value}) did not raise")


def _write_domain_layout(root: Path):
    project_uid = "project_" + "1" * 32
    page_uid = "page_" + "2" * 32
    root_uid = "node_" + "3" * 32
    coma_node_uid = "node_" + "4" * 32
    coma_uid = "coma_" + "5" * 32
    root.mkdir()
    (root / "project.json").write_text(
        json.dumps(
            {
                "schema": "bmanga.project",
                "schemaVersion": 1,
                "projectUid": project_uid,
                "revision": 1,
                "settings": {},
                "pageOrder": [page_uid],
                "pages": {
                    page_uid: {
                        "uid": page_uid,
                        "displayId": "p0001",
                        "displayNumber": 1,
                        "title": "",
                        "spread": False,
                        "sourcePageUids": [],
                        "settings": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    page_dir = root / "pages" / page_uid
    page_dir.mkdir(parents=True)
    (page_dir / "page.json").write_text(
        json.dumps(
            {
                "schema": "bmanga.page",
                "schemaVersion": 1,
                "projectUid": project_uid,
                "pageUid": page_uid,
                "revision": 1,
                "settings": {},
                "tree": {
                    "rootUid": root_uid,
                    "nodes": {
                        root_uid: {
                            "uid": root_uid,
                            "kind": "page",
                            "displayId": "p0001",
                            "title": "",
                            "settings": {},
                            "nativeUid": "",
                        },
                        coma_node_uid: {
                            "uid": coma_node_uid,
                            "kind": "coma",
                            "displayId": "c01",
                            "title": "",
                            "settings": {},
                            "nativeUid": coma_uid,
                        },
                    },
                    "children": {
                        root_uid: [coma_node_uid],
                        coma_node_uid: [],
                    },
                },
                "links": {},
            }
        ),
        encoding="utf-8",
    )
    return page_uid, coma_uid


def test_new_layout_paths(tmp_path):
    paths = _load_paths()
    root = tmp_path / "Test.bmanga"
    page_uid, coma_uid = _write_domain_layout(root)
    assert paths.project_meta_path(root) == root / "project.json"
    assert paths.page_dir(root, "p0001") == root / "pages" / page_uid
    assert paths.page_dir(root, page_uid) == root / "pages" / page_uid
    coma_dir = root / "pages" / page_uid / "comas" / coma_uid
    assert paths.coma_dir(root, "p0001", "c01") == coma_dir
    assert paths.coma_blend_path(root, page_uid, coma_uid) == coma_dir / "scene.blend"
    assert paths.coma_thumb_path(root, page_uid, coma_uid) == coma_dir / "preview.png"
    assert paths.coma_preview_path(root, page_uid, coma_uid) == coma_dir / "preview.png"
    assert paths.coma_passes_dir(root, page_uid, coma_uid) == coma_dir / "passes"
    assert paths.coma_passes_cube_dir(root, page_uid, coma_uid) == coma_dir / "passes" / "cube"
    assert not hasattr(paths, "coma_json_path")


def test_domain_paths_follow_atomic_replacement(tmp_path):
    paths = _load_paths()
    root = tmp_path / "Cache.bmanga"
    page_uid, coma_uid = _write_domain_layout(root)
    assert paths.resolve_page_uid(root, "p0001") == page_uid
    assert paths.resolve_coma_uid(root, "p0001", "c01") == coma_uid

    project_path = root / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["pages"][page_uid]["displayId"] = "p0002"
    replacement = root / ".project.json.replace"
    replacement.write_text(json.dumps(project), encoding="utf-8")
    replacement.replace(project_path)
    assert paths.resolve_page_uid(root, "p0002") == page_uid

    page_path = root / "pages" / page_uid / "page.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    new_coma_uid = "coma_" + "6" * 32
    coma_node = next(
        node
        for node in page["tree"]["nodes"].values()
        if node["kind"] == "coma"
    )
    coma_node["nativeUid"] = new_coma_uid
    replacement = page_path.with_name(".page.json.replace")
    replacement.write_text(json.dumps(page), encoding="utf-8")
    replacement.replace(page_path)
    assert paths.resolve_coma_uid(root, page_uid, "c01") == new_coma_uid


def test_page_junction_is_rejected_by_common_path_helpers(tmp_path):
    paths = _load_paths()
    root = tmp_path / "Boundary.bmanga"
    page_uid, coma_uid = _write_domain_layout(root)
    redirected = root / "pages" / page_uid
    page_payload = (redirected / "page.json").read_bytes()
    (redirected / "page.json").unlink()
    redirected.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "page.json").write_bytes(page_payload)

    if os.name == "nt":
        result = subprocess.run(
            [
                "cmd",
                "/c",
                "mklink",
                "/J",
                str(redirected),
                str(outside),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
    else:
        os.symlink(outside, redirected, target_is_directory=True)

    callbacks = (
        lambda: paths.page_dir(root, page_uid),
        lambda: paths.page_dir(root, "p0001"),
        lambda: paths.page_meta_path(root, page_uid),
        lambda: paths.page_assets_dir(root, page_uid),
        lambda: paths.page_comas_dir(root, page_uid),
        lambda: paths.coma_dir(root, page_uid, coma_uid),
    )
    for callback in callbacks:
        with pytest.raises(
            paths.WorkPathBoundaryError,
            match="escapes project root",
        ):
            callback()
    assert not (outside / "assets").exists()
    assert not (outside / "comas").exists()


if __name__ == "__main__":
    import tempfile

    test_page_and_coma_ids()
    test_invalid_ids_raise()
    with tempfile.TemporaryDirectory() as directory:
        test_new_layout_paths(Path(directory))
