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
BASELINE_PATH = ROOT / "docs" / "refactor" / "phase0" / "performance_baseline.json"
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
_REPOSITORY_PAGE_READ_PATHS: list[Path] = []
FIXTURE_CONTRACT = {
    "pages": PAGES,
    "comas_per_page": COMAS_PER_PAGE,
    "balloons_per_page": COMAS_PER_PAGE,
    "texts_per_page": COMAS_PER_PAGE,
    "coma_mesh_objects": 4,
}
_ENVIRONMENT_KEYS = (
    "windows_build",
    "cpu",
    "logical_cpu_count",
    "ram_bytes",
    "gpus",
    "display_pixels",
    "blender_version",
    "blender_build_hash",
    "ui_scale",
)


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


def _stats(values: list[float]) -> dict[str, object]:
    return {
        "trials": len(values),
        "samples_ms": [round(value, 3) for value in values],
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3),
        "min_ms": round(min(values), 3),
    }


def _fixture_coma_payload(index: int) -> dict[str, object]:
    return {
        "id": f"c{index + 1:02d}",
        "comaId": f"c{index + 1:02d}",
        "shape": {
            "type": "rect",
            "rect": {
                "x": 15.0 + (index % 2) * 95.0,
                "y": 200.0 - (index // 2) * 90.0,
                "widthMm": 85.0,
                "heightMm": 80.0,
            },
        },
    }


def _fixture_balloon_payload(page_no: int, index: int) -> dict[str, object]:
    return {
        "id": f"balloon_{page_no:04d}_{index:02d}",
        "shape": "ellipse" if index % 2 == 0 else "cloud",
        "xMm": 20.0 + (index % 2) * 95.0,
        "yMm": 205.0 - (index // 2) * 90.0,
        "widthMm": 42.0,
        "heightMm": 30.0,
        "tails": [
            {
                "type": "straight",
                "directionDeg": 250.0,
                "lengthMm": 9.0,
            }
        ],
    }


def _fixture_text_payload(page_no: int, index: int) -> dict[str, object]:
    balloon_x = 20.0 + (index % 2) * 95.0
    balloon_y = 205.0 - (index // 2) * 90.0
    return {
        "id": f"text_{page_no:04d}_{index:02d}",
        "body": f"基準ページ {page_no}、コマ {index + 1}\n保存再読込の日本語",
        "xMm": balloon_x + 8.0,
        "yMm": balloon_y + 6.0,
        "widthMm": 26.0,
        "heightMm": 18.0,
    }


def _fixture_page_payload(page_id: str, page_no: int) -> dict[str, object]:
    return {
        "schemaVersion": 3,
        "id": page_id,
        "spread": False,
        "comas": [_fixture_coma_payload(index) for index in range(COMAS_PER_PAGE)],
        "balloons": [
            _fixture_balloon_payload(page_no, index)
            for index in range(COMAS_PER_PAGE)
        ],
        "texts": [
            _fixture_text_payload(page_no, index)
            for index in range(COMAS_PER_PAGE)
        ],
    }


def _fixture_page_document(
    page,
    page_payload,
    project_document,
    work_payload,
    domain_projection,
):
    page_uid = domain_projection.ensure_page_uid(
        page,
        project_document.project_uid,
    )
    coma_uids = {
        str(coma.get("comaId", "") or ""): domain_projection.derived_uid(
            domain_projection.UIDKind.COMA,
            page_uid,
            str(coma.get("comaId", "") or ""),
        )
        for coma in page_payload.get("comas", ())
    }
    return domain_projection.page_document_from_payload(
        project_uid=project_document.project_uid,
        page_uid=page_uid,
        revision=0,
        work_payload=work_payload,
        page_payload=page_payload,
        coma_uids=coma_uids,
    )


def _checkpoint_fixture_pages(
    work_root: Path,
    work,
    page_payloads: tuple[dict[str, object], ...],
) -> None:
    domain_projection = _sub("io.domain_projection")
    domain_runtime = _sub("io.domain_runtime")
    schema = _sub("io.schema")
    project_document = domain_projection.project_document_from_work(work)
    work_payload = schema.work_to_dict(work)
    page_documents = tuple(
        _fixture_page_document(
            page,
            page_payload,
            project_document,
            work_payload,
            domain_projection,
        )
        for page, page_payload in zip(
            work.pages,
            page_payloads,
            strict=True,
        )
    )
    repository = domain_runtime.repository_for(work_root)
    repository.checkpoint(project_document, page_documents)
    store = domain_runtime.install_store(
        work_root,
        project_document,
        page_documents,
    )
    domain_projection.bind_project_document(work, project_document)
    for page, document in zip(work.pages, page_documents, strict=True):
        domain_projection.bind_page_document(page, document)
    store.mark_checkpointed(
        project=True,
        page_uids=tuple(document.page_uid for document in page_documents),
    )


def _populate_work_project(work_root: Path) -> None:
    result = bpy.ops.bmanga.work_new(filepath=str(work_root))
    if result != {"FINISHED"}:
        raise RuntimeError(f"基準作品を作成できません: {result}")
    work = bpy.context.scene.bmanga_work
    page_io = _sub("io.page_io")
    while len(work.pages) < PAGES:
        entry = page_io.register_new_page(work)
        page_io.ensure_page_dir(work_root, entry)
    page_payloads = tuple(
        _fixture_page_payload(str(page.id), index + 1)
        for index, page in enumerate(work.pages)
    )
    for page in work.pages:
        page.coma_count = COMAS_PER_PAGE
    _checkpoint_fixture_pages(work_root, work, page_payloads)
    page_detail = _sub("utils.page_detail")
    for page in work.pages:
        page_detail.clear_page_detail(page)
    # Phase 4以降のsave_preはDomain確定だけを担当し、全Object走査をしない。
    # fixture作成中だけ生成したページ詳細の派生実体は、実運用のwork.blendと
    # 同じ軽量状態へ明示的に戻してから性能基準ファイルを保存する。
    page_file_scene = _sub("utils.page_file_scene")
    page_file_scene.purge_work_list_runtime_data(bpy.context.scene)
    bpy.ops.wm.save_as_mainfile(filepath=str(work_root / "work.blend"))
    _capture_baseline(work_root, work_root / "work.blend")


def _capture_baseline(work_root: Path, blend_path: Path) -> None:
    baseline = _sub("io.save_baseline")
    page_paths = sorted(work_root.glob("pages/*/page.json"))
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
    paths = _sub("utils.paths")
    if bpy.ops.bmanga.open_page_file("EXEC_DEFAULT", index=0) != {"FINISHED"}:
        raise RuntimeError("代表page.blendを作成できません")
    page_blend = paths.page_blend_path(work_root, "p0001")
    bpy.ops.wm.save_as_mainfile(filepath=str(page_blend))
    _capture_baseline(work_root, page_blend)
    work = bpy.context.scene.bmanga_work
    work.active_page_index = 0
    page = work.pages[0]
    coma_index = next(
        index
        for index, coma in enumerate(page.comas)
        if str(getattr(coma, "coma_id", "") or getattr(coma, "id", "") or "") == "c01"
    )
    _sub("utils.active_target").focus_active_coma(
        bpy.context.scene,
        work,
        0,
        coma_index,
    )
    if bpy.ops.bmanga.enter_coma_mode("EXEC_DEFAULT") != {"FINISHED"}:
        raise RuntimeError("代表c01.blendを作成できません")
    _add_representative_coma_geometry()
    bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)
    return work_root


def _role_path(project: Path, role: str) -> Path:
    paths = _sub("utils.paths")
    if role == "work":
        return project / "work.blend"
    if role == "page":
        return paths.page_blend_path(project, "p0001")
    coma_root = paths.page_dir(project, "p0001") / paths.COMAS_DIR_NAME
    scenes = sorted(coma_root.glob(f"*/{paths.COMA_BLEND_NAME}"))
    if len(scenes) != 1:
        raise AssertionError(f"代表コマ実体が一意ではありません: {scenes}")
    return scenes[0]


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _open_once(path: Path, role: str) -> tuple[float, dict[str, object]]:
    _JSON_READ_PATHS.clear()
    _REPOSITORY_PAGE_READ_PATHS.clear()
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
    repository_module = _sub("bmanga_core.domain_repository")
    repository_type = repository_module.ProjectRepository
    original_load_page = repository_type.load_page

    def counted_load_page(repository, page_uid):
        _REPOSITORY_PAGE_READ_PATHS.append(
            repository.page_path(page_uid).resolve()
        )
        return original_load_page(repository, page_uid)

    repository_type.load_page = counted_load_page


def _detail_identity(path: Path) -> tuple[str, str, str]:
    if path.name == "page.json" and path.parent.parent.name == "pages":
        paths = _sub("utils.paths")
        work_root = path.parents[2]
        return "page", paths.page_display_id(work_root, path.parent.name), ""
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
    repository_pages = [
        _detail_identity(path)[1]
        for path in _REPOSITORY_PAGE_READ_PATHS
    ]
    page_details.extend(
        page_id_value
        for page_id_value in repository_pages
        if page_id_value
    )
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
        "repository_page_read_paths": [
            str(path) for path in _REPOSITORY_PAGE_READ_PATHS
        ],
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
        "json_read_names": [
            [Path(path).name for path in row["json_read_paths"]]
            for row in rows
        ],
        "repository_page_read_counts": [
            len(row["repository_page_read_paths"]) for row in rows
        ],
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
    contract_bytes = json.dumps(
        FIXTURE_CONTRACT,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_version": 2,
        "blender_version": bpy.app.version_string,
        "fixture_tree_sha256": fixture_hash,
        "fixture_contract": FIXTURE_CONTRACT,
        "fixture_contract_sha256": hashlib.sha256(
            contract_bytes
        ).hexdigest(),
        "environment": _environment(),
        "trials_per_condition": TRIALS,
        "cold_definition": (
            "同一内容を別path・別file blockへ複製し、各pathを初回open。"
            "OS page cacheの強制破棄は行わない"
        ),
        "warm_definition": "同一pathを1回warm-up後、同一processで20回open",
        "results": results,
    }


def _baseline_comparison(payload: dict[str, object]) -> dict[str, object]:
    baseline_root = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    baseline = baseline_root["open"]
    baseline_contract = baseline["fixture_contract"]
    contract_bytes = json.dumps(
        baseline_contract,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    baseline_environment = baseline["environment"]
    current_environment = payload["environment"]
    environment_unverified = {}
    if not baseline_environment.get("gpus") and current_environment.get("gpus"):
        environment_unverified["gpus"] = {
            "baseline": baseline_environment.get("gpus"),
            "current": current_environment.get("gpus"),
            "reason": "Phase 0 GPU probe returned no data",
        }
    environment_mismatches = {
        key: {
            "baseline": baseline_environment.get(key),
            "current": current_environment.get(key),
        }
        for key in _ENVIRONMENT_KEYS
        if key not in environment_unverified
        and baseline_environment.get(key) != current_environment.get(key)
    }
    rows = []
    regressions = []
    for role in ("work", "page", "coma"):
        for condition in ("path_cold", "warm"):
            expected = float(
                baseline["results"][role][condition]["p95_ms"]
            )
            actual = float(
                payload["results"][role][condition]["p95_ms"]
            )
            row = {
                "role": role,
                "condition": condition,
                "baseline_p95_ms": expected,
                "current_p95_ms": actual,
                "ratio": round(actual / expected, 4),
                "passed": actual <= expected,
            }
            rows.append(row)
            if not row["passed"]:
                regressions.append(row)
    return {
        "baseline_path": BASELINE_PATH.relative_to(ROOT).as_posix(),
        "baseline_fixture_tree_sha256": baseline["fixture_tree_sha256"],
        "current_fixture_tree_sha256": payload["fixture_tree_sha256"],
        "baseline_fixture_contract_sha256": hashlib.sha256(
            contract_bytes
        ).hexdigest(),
        "current_fixture_contract_sha256": payload[
            "fixture_contract_sha256"
        ],
        "fixture_contract_matches": (
            baseline_contract == payload["fixture_contract"]
        ),
        "environment_unverified": environment_unverified,
        "environment_mismatches": environment_mismatches,
        "rows": rows,
        "regressions": regressions,
    }


def _assert_gate(payload: dict[str, object]) -> None:
    comparison = payload["baseline_comparison"]
    assert comparison["fixture_contract_matches"], (
        "Phase 0 fixture contract differs from current fixture"
    )
    assert (
        comparison["baseline_fixture_contract_sha256"]
        == comparison["current_fixture_contract_sha256"]
    )
    assert not comparison["environment_mismatches"], (
        "Phase 0 performance environment differs: "
        f"{comparison['environment_mismatches']}"
    )
    assert not comparison["regressions"], (
        "Phase 0 P95 regression detected: "
        f"{comparison['regressions']}"
    )
    for role, result in payload["results"].items():
        cold = result["path_cold_observation"]
        warm = result["warm_observation"]
        assert cold["max_unrelated_page_detail_loads"] == 0, role
        assert warm["max_unrelated_page_detail_loads"] == 0, role
        assert cold["max_unrelated_coma_detail_loads"] == 0, role
        assert warm["max_unrelated_coma_detail_loads"] == 0, role
        expected_page_reads = 0 if role == "work" else 1
        assert set(cold["repository_page_read_counts"]) == {
            expected_page_reads
        }, role
        assert set(warm["repository_page_read_counts"]) == {
            expected_page_reads
        }, role
        assert set(warm["json_read_counts"]) == {0}, role


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
        payload["baseline_comparison"] = _baseline_comparison(payload)
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _assert_gate(payload)
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
