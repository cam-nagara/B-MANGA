"""Phase 0: work/page/comaのopen時間を同一fixtureで測定する。"""

from __future__ import annotations

import importlib
import importlib.util
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy


ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = Path(
    os.environ.get(
        "BMANGA_PHASE0_OPEN_PERF_OUT",
        str(
            ROOT
            / "_verify"
            / "2026-07-28_full_refactor_phase0"
            / "open_performance.json"
        ),
    )
)
MODULE_NAME = "bmanga_phase0_open_perf"
TRIALS = 20
PAGES = 55
COMAS_PER_PAGE = 5
_JSON_READ_PATHS: list[Path] = []


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        MODULE_NAME,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("B-MANGAを読み込めません")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    module.register()
    return module


def _sub(path: str):
    return importlib.import_module(f"{MODULE_NAME}.{path}")


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(
        0,
        min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1),
    )
    return ordered[index]


def _stats(values: list[float]) -> dict[str, float | int]:
    return {
        "trials": len(values),
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3),
        "min_ms": round(min(values), 3),
    }


def _fill_page(page, page_no: int) -> None:
    page.comas.clear()
    page.balloons.clear()
    page.texts.clear()
    for index in range(COMAS_PER_PAGE):
        coma = page.comas.add()
        coma.id = f"c{index + 1:02d}"
        coma.coma_id = coma.id
        coma.rect_x_mm = 15.0 + (index % 2) * 95.0
        coma.rect_y_mm = 200.0 - (index // 2) * 90.0
        coma.rect_width_mm = 85.0
        coma.rect_height_mm = 80.0
        _add_balloon_and_text(page, page_no, index)
    page.coma_count = len(page.comas)


def _add_balloon_and_text(page, page_no: int, index: int) -> None:
    balloon = page.balloons.add()
    balloon.id = f"balloon_{page_no:04d}_{index:02d}"
    balloon.shape = "ellipse" if index % 2 == 0 else "cloud"
    balloon.x_mm = 20.0 + (index % 2) * 95.0
    balloon.y_mm = 205.0 - (index // 2) * 90.0
    balloon.width_mm = 42.0
    balloon.height_mm = 30.0
    tail = balloon.tails.add()
    tail.type = "straight"
    tail.direction_deg = 250.0
    tail.length_mm = 9.0
    text = page.texts.add()
    text.id = f"text_{page_no:04d}_{index:02d}"
    text.body = f"基準ページ {page_no}、コマ {index + 1}\n保存再読込の日本語"
    text.x_mm = balloon.x_mm + 8.0
    text.y_mm = balloon.y_mm + 6.0
    text.width_mm = 26.0
    text.height_mm = 18.0


def _populate_work_project(work_root: Path) -> None:
    result = bpy.ops.bmanga.work_new(filepath=str(work_root))
    if result != {"FINISHED"}:
        raise RuntimeError(f"基準作品を作成できません: {result}")
    work = bpy.context.scene.bmanga_work
    while len(work.pages) < PAGES:
        if "FINISHED" not in bpy.ops.bmanga.page_add("EXEC_DEFAULT"):
            raise RuntimeError("55ページfixtureの追加に失敗しました")
    page_detail = _sub("utils.page_detail")
    page_io = _sub("io.page_io")
    for index, page in enumerate(work.pages):
        page_detail.ensure_page_detail(work, page)
        _fill_page(page, index + 1)
        page_io.save_page_json(work_root, page)
    page_io.save_pages_json(work_root, work)
    for page in work.pages:
        page_detail.clear_page_detail(page)
    bpy.ops.wm.save_as_mainfile(filepath=str(work_root / "work.blend"))
    _capture_baseline(work_root, work_root / "work.blend")


def _capture_baseline(work_root: Path, blend_path: Path) -> None:
    baseline = _sub("io.project_content_save_baseline")
    page_paths = sorted(work_root.glob("p????/page.json"))
    baseline.capture_loaded_baseline(
        work_root,
        blend_path,
        page_json_paths=page_paths,
    )


def _add_representative_coma_geometry() -> None:
    for index in range(4):
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32,
            ring_count=16,
            location=(float(index) * 2.5, 0.0, 0.0),
        )
        obj = bpy.context.active_object
        if obj is not None:
            obj.name = f"Phase0CharacterPart_{index:02d}"


def _fixture_project(root: Path) -> Path:
    work_root = root / "Phase0Open.bmanga"
    _populate_work_project(work_root)
    if bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) != {"FINISHED"}:
        raise RuntimeError("代表page.blendを作成できません")
    page_blend = work_root / "p0001" / "page.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(page_blend))
    _capture_baseline(work_root, page_blend)
    work = bpy.context.scene.bmanga_work
    work.active_page_index = 0
    work.pages[0].active_coma_index = 0
    if bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT") != {"FINISHED"}:
        raise RuntimeError("代表c01.blendを作成できません")
    _add_representative_coma_geometry()
    bpy.ops.wm.save_as_mainfile(filepath=str(work_root / "p0001" / "c01" / "c01.blend"))
    return work_root


def _role_path(project: Path, role: str) -> Path:
    if role == "work":
        return project / "work.blend"
    if role == "page":
        return project / "p0001" / "page.blend"
    return project / "p0001" / "c01" / "c01.blend"


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _open_once(path: Path, role: str) -> tuple[float, dict[str, object]]:
    _JSON_READ_PATHS.clear()
    started = time.perf_counter()
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
    elapsed = (time.perf_counter() - started) * 1000.0
    page_file_scene = _sub("utils.page_file_scene")
    actual_role, page_id, coma_id = page_file_scene.current_role()
    if actual_role != role:
        raise AssertionError(f"role mismatch: {role} != {actual_role}")
    return elapsed, _read_observation(role, page_id, coma_id)


def _measurement_targets(
    root: Path,
    source_project: Path,
) -> dict[str, dict[str, object]]:
    targets: dict[str, dict[str, object]] = {}
    for role in ("coma", "page", "work"):
        cold = []
        for index in range(TRIALS):
            project = root / "_path_cold" / role / f"run_{index:02d}" / source_project.name
            shutil.copytree(source_project, project)
            cold.append(_role_path(project, role))
        warm_project = root / "_warm" / role / source_project.name
        shutil.copytree(source_project, warm_project)
        targets[role] = {"cold": cold, "warm": _role_path(warm_project, role)}
    return targets


def _trials(paths: list[Path], role: str) -> tuple[list[float], list[dict[str, object]]]:
    measurements = [_open_once(path, role) for path in paths]
    return (
        [elapsed for elapsed, _observation in measurements],
        [observation for _elapsed, observation in measurements],
    )


def _warm_trials(path: Path, role: str) -> tuple[list[float], list[dict[str, object]]]:
    _open_once(path, role)
    return _trials([path] * TRIALS, role)


def _install_load_counters() -> None:
    json_io = _sub("utils.json_io")
    original_read = json_io.read_json

    def counted(path):
        _JSON_READ_PATHS.append(Path(path).resolve())
        return original_read(path)

    json_io.read_json = counted


def _detail_identity(path: Path) -> tuple[str, str, str]:
    if path.name == "page.json" and path.parent.name.startswith("p"):
        return "page", path.parent.name, ""
    if (
        path.suffix.lower() == ".json"
        and path.stem == path.parent.name
        and path.parent.name.startswith("c")
        and path.parent.parent.name.startswith("p")
    ):
        return "coma", path.parent.parent.name, path.parent.name
    return "", "", ""


def _read_observation(role: str, page_id: str, coma_id: str) -> dict[str, object]:
    page_details = []
    coma_details = []
    sidecars = {"work_json": 0, "pages_json": 0}
    for path in _JSON_READ_PATHS:
        if path.name in ("work.json", "pages.json"):
            sidecars[path.name.replace(".", "_")] += 1
        kind, loaded_page, loaded_coma = _detail_identity(path)
        if kind == "page":
            page_details.append(loaded_page)
        elif kind == "coma":
            coma_details.append((loaded_page, loaded_coma))
    unrelated_pages = [
        item
        for item in page_details
        if role == "work" or not page_id or item != page_id
    ]
    unrelated_comas = [
        item
        for item in coma_details
        if role != "coma" or item != (page_id, coma_id)
    ]
    return {
        "json_read_paths": [str(path) for path in _JSON_READ_PATHS],
        "page_detail_load_ids": page_details,
        "coma_detail_load_ids": ["/".join(item) for item in coma_details],
        "unrelated_page_detail_loads": len(unrelated_pages),
        "unrelated_coma_detail_loads": len(unrelated_comas),
        "sidecar_load_counts": sidecars,
    }


def _observation_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "trials": len(rows),
        "max_unrelated_page_detail_loads": max(
            int(row["unrelated_page_detail_loads"]) for row in rows
        ),
        "page_detail_load_counts": [
            len(row["page_detail_load_ids"]) for row in rows
        ],
        "max_unrelated_coma_detail_loads": max(
            int(row["unrelated_coma_detail_loads"]) for row in rows
        ),
        "coma_detail_load_counts": [
            len(row["coma_detail_load_ids"]) for row in rows
        ],
        "json_read_counts": [len(row["json_read_paths"]) for row in rows],
        "sidecar_load_counts": [
            row["sidecar_load_counts"] for row in rows
        ],
    }


def _memory_bytes() -> int:
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return int(status.total_physical)


def _gpu_info() -> list[dict[str, str]]:
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name,DriverVersion | ConvertTo-Json -Compress"
        ),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    data = json.loads(completed.stdout)
    rows = data if isinstance(data, list) else [data]
    return [
        {"name": str(row.get("Name", "")), "driver": str(row.get("DriverVersion", ""))}
        for row in rows
    ]


def _environment() -> dict[str, object]:
    import ctypes

    build_hash = bpy.app.build_hash
    return {
        "os": platform.platform(),
        "windows_build": platform.version(),
        "cpu": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "ram_bytes": _memory_bytes(),
        "gpus": _gpu_info(),
        "display_pixels": [
            ctypes.windll.user32.GetSystemMetrics(0),
            ctypes.windll.user32.GetSystemMetrics(1),
        ],
        "blender_version": bpy.app.version_string,
        "blender_build_hash": (
            build_hash.decode("ascii", errors="replace")
            if isinstance(build_hash, bytes)
            else str(build_hash)
        ),
        "ui_scale": float(bpy.context.preferences.system.ui_scale),
    }


def _measure_results(
    project: Path, targets: dict[str, dict[str, object]]
) -> dict[str, object]:
    results = {}
    for role in ("coma", "page", "work"):
        role_targets = targets[role]
        path_cold, cold_observations = _trials(role_targets["cold"], role)
        warm, warm_observations = _warm_trials(role_targets["warm"], role)
        source_path = _role_path(project, role)
        results[role] = {
            "file_bytes": source_path.stat().st_size,
            "path_cold": _stats(path_cold),
            "warm": _stats(warm),
            "path_cold_observation": _observation_summary(cold_observations),
            "warm_observation": _observation_summary(warm_observations),
        }
    return results


def _payload(fixture_hash: str, results: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 2,
        "blender_version": bpy.app.version_string,
        "fixture_tree_sha256": fixture_hash,
        "fixture_contract": {
            "pages": PAGES,
            "comas_per_page": COMAS_PER_PAGE,
            "balloons_per_page": COMAS_PER_PAGE,
            "texts_per_page": COMAS_PER_PAGE,
            "coma_mesh_objects": 4,
        },
        "environment": _environment(),
        "trials_per_condition": TRIALS,
        "cold_definition": (
            "同一内容を別path・別file blockへ複製し、各pathを初回open。"
            "OS page cacheの強制破棄は行わない"
        ),
        "warm_definition": "同一pathを1回warm-up後、同一processで20回open",
        "results": results,
    }


def main() -> None:
    module = None
    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_phase0_open_"))
    try:
        module = _load_addon()
        project = _fixture_project(temp_root)
        fixture_hash = _tree_hash(project)
        targets = _measurement_targets(temp_root, project)
        _install_load_counters()
        payload = _payload(fixture_hash, _measure_results(project, targets))
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"BMANGA_PHASE0_OPEN_PERFORMANCE_OK {OUT_PATH}", flush=True)
    finally:
        if module is not None:
            try:
                module.unregister()
            except Exception:
                traceback.print_exc()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
    sys.stdout.flush()
    os._exit(0)
