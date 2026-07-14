from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "utils" / "spread_metadata.py"
    spec = importlib.util.spec_from_file_location("bmanga_spread_metadata_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


METADATA = _module()


def _page(page_id: str, x: float) -> dict:
    return {
        "schemaVersion": 4,
        "id": page_id,
        "title": f"title-{page_id}",
        "offsetXMm": x / 10.0,
        "spread": False,
        "activeComaIndex": 0,
        "activeBalloonIndex": 0,
        "activeTextIndex": 0,
        "comas": [{
            "id": "c01",
            "comaId": "c01",
            "shape": {
                "type": "rect",
                "rect": {"x": x, "y": 1.0, "widthMm": 20.0, "heightMm": 30.0},
                "vertices": [],
            },
            "layerRefs": ["gp_shared", "balloon_0001", "text_0001"],
        }],
        "balloons": [{
            "id": "balloon_0001", "textId": "text_0001", "xMm": x, "widthMm": 10.0
        }],
        "texts": [{
            "id": "text_0001", "parentBalloonId": "balloon_0001", "xMm": x, "widthMm": 5.0
        }],
    }


def test_merge_split_roundtrip_preserves_collision_maps_and_refs():
    first = _page("p0001", 10.0)
    second = _page("p0002", 20.0)
    merged, id_maps, parts = METADATA.merge_pages(
        first,
        second,
        first_page_id="p0001",
        second_page_id="p0002",
        spread_id="p0001-0002",
        right_offset_mm=100.0,
    )
    assert [item["comaId"] for item in merged["comas"]] == ["c01", "c02"]
    assert [item["id"] for item in merged["balloons"]] == ["balloon_0001", "balloon_0002"]
    assert [item["id"] for item in merged["texts"]] == ["text_0001", "text_0002"]
    assert merged["comas"][0]["shape"]["rect"]["x"] == 110.0
    assert merged["balloons"][0]["xMm"] == 110.0
    assert merged["texts"][0]["xMm"] == 110.0
    assert merged["balloons"][1]["textId"] == "text_0002"
    assert merged["texts"][1]["parentBalloonId"] == "balloon_0002"

    # 子Blenderが解決する GP 衝突も page.json の参照へ反映された状態を模擬する。
    merged["comas"][1]["layerRefs"][0] = "gp_second"
    manifest = {
        "sourcePages": ["p0001", "p0002"],
        "idMaps": id_maps,
        "objectMaps": {
            "p0001": id_maps["p0001"],
            "p0002": {**id_maps["p0002"], "gp": {"gp_shared": "gp_second"}},
        },
        **parts,
    }
    memberships = METADATA.source_memberships(manifest)
    assert "gp_second" in memberships["p0002"]["gp"]
    assert "balloon_0002" in memberships["p0002"]["balloon"]
    split = METADATA.split_page(
        merged,
        manifest,
        first_page_id="p0001",
        second_page_id="p0002",
        spread_id="p0001-0002",
        right_offset_mm=100.0,
    )
    assert split["p0001"]["comas"] == first["comas"]
    assert split["p0001"]["balloons"] == first["balloons"]
    assert split["p0001"]["texts"] == first["texts"]
    assert split["p0002"]["comas"] == second["comas"]
    assert split["p0002"]["balloons"] == second["balloons"]
    assert split["p0002"]["texts"] == second["texts"]
    assert split["p0001"]["title"] == "title-p0001"
    assert split["p0002"]["title"] == "title-p0002"
    assert split["p0001"]["offsetXMm"] == first["offsetXMm"]
    assert split["p0002"]["offsetXMm"] == second["offsetXMm"]
    assert split["p0002"]["activeComaIndex"] == 0


def test_reverse_link_groups_restores_source_names():
    manifest = {
        "linkGroupMaps": {
            "p0001": {"shared": "shared"},
            "p0002": {"shared": "layer_link_remapped"},
        }
    }
    assert METADATA.reverse_link_groups_for_source(manifest, "p0001") == {
        "shared": "shared"
    }
    assert METADATA.reverse_link_groups_for_source(manifest, "p0002") == {
        "layer_link_remapped": "shared"
    }


def test_split_coma_storage_map_restores_colliding_directory_id():
    manifest = {
        "idMaps": {
            "p0002": {"coma": {"c01": "c02"}},
        }
    }
    assert METADATA.coma_storage_map_for_source(manifest, "p0002") == {
        "c02": "c01"
    }


def test_global_parent_and_coordinates_roundtrip():
    work = {
        "image_layers": [
            {"id": "image_a", "parentKey": "p0001:c01", "xMm": 4.0},
            {"id": "image_b", "parentKey": "p0002", "xMm": 8.0},
            {"id": "image_nested", "parentKey": "folder_a", "folderKey": "folder_a", "xMm": 6.0},
        ],
        "fill_layers": [],
        "raster_layers": [],
        "image_path_layers": [],
        "layer_folders": [
            {"id": "folder_a", "parentKey": "p0001"},
            {"id": "folder_child", "parentKey": "folder_a"},
        ],
    }
    coma_maps = {"p0001": {"c01": "c01"}, "p0002": {"c01": "c02"}}
    merged, sources = METADATA.merge_work_data(
        work,
        first_page_id="p0001",
        second_page_id="p0002",
        spread_id="p0001-0002",
        coma_maps=coma_maps,
        right_offset_mm=100.0,
    )
    assert merged["image_layers"][0]["parentKey"] == "p0001-0002:c01"
    assert merged["image_layers"][0]["xMm"] == 104.0
    assert merged["image_layers"][1]["parentKey"] == "p0001-0002"
    assert merged["image_layers"][2]["parentKey"] == "folder_a"
    assert merged["image_layers"][2]["xMm"] == 106.0
    restored = METADATA.split_work_data(
        merged,
        {"globalSources": sources, "idMaps": {
            "p0001": {"coma": coma_maps["p0001"]},
            "p0002": {"coma": coma_maps["p0002"]},
        }},
        first_page_id="p0001",
        second_page_id="p0002",
        spread_id="p0001-0002",
        right_offset_mm=100.0,
    )
    assert restored == work


def test_split_blocks_content_without_source_marker():
    first = _page("p0001", 10.0)
    second = _page("p0002", 20.0)
    merged, id_maps, parts = METADATA.merge_pages(
        first,
        second,
        first_page_id="p0001",
        second_page_id="p0002",
        spread_id="p0001-0002",
        right_offset_mm=100.0,
    )
    merged["texts"].append({"id": "text_new", "xMm": 2.0})
    with pytest.raises(METADATA.SpreadMetadataError, match="所属元"):
        METADATA.split_page(
            merged,
            {"idMaps": id_maps, "objectMaps": {}, **parts},
            first_page_id="p0001",
            second_page_id="p0002",
            spread_id="p0001-0002",
            right_offset_mm=100.0,
        )


def test_merge_blocks_invalid_coma_id_and_global_collision():
    bad = _page("p0001", 0.0)
    bad["comas"][0]["comaId"] = "c100"
    with pytest.raises(METADATA.SpreadMetadataError, match="コマID"):
        METADATA.merge_pages(
            bad,
            _page("p0002", 0.0),
            first_page_id="p0001",
            second_page_id="p0002",
            spread_id="p0001-0002",
            right_offset_mm=100.0,
        )
    work = {
        "image_layers": [
            {"id": "same", "parentKey": "p0001"},
            {"id": "same", "parentKey": "p0002"},
        ]
    }
    with pytest.raises(METADATA.SpreadMetadataError, match="衝突"):
        METADATA.merge_work_data(
            work,
            first_page_id="p0001",
            second_page_id="p0002",
            spread_id="p0001-0002",
            coma_maps={"p0001": {}, "p0002": {}},
            right_offset_mm=100.0,
        )
