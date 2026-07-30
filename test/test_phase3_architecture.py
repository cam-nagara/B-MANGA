from __future__ import annotations

import ast
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOTS = (
    ROOT / "bmanga_core",
    ROOT / "core",
    ROOT / "io",
    ROOT / "keymap",
    ROOT / "operators",
    ROOT / "panels",
    ROOT / "typography",
    ROOT / "ui",
    ROOT / "utils",
    ROOT / "addons",
)
REMOVED_MIGRATION_MODULES = (
    "io/balloon_white_outline_migration.py",
    "io/detail_data_blender_migration.py",
    "io/detail_data_blender_worker_runtime.py",
    "io/detail_data_migration_manifest.py",
    "io/project_content_migration.py",
    "io/project_content_migration_capacity.py",
    "io/project_content_migration_lock.py",
    "io/project_content_migration_manifest.py",
    "io/project_content_migration_model.py",
    "io/project_content_migration_recovery.py",
    "io/project_content_migration_storage.py",
    "io/project_content_version.py",
    "operators/detail_data_migration_op.py",
)


def _product_sources() -> list[Path]:
    return sorted(
        path
        for directory in PRODUCT_ROOTS
        for path in directory.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_removed_old_project_migration_modules_and_operator_stay_absent():
    assert not [path for relative in REMOVED_MIGRATION_MODULES if (path := ROOT / relative).exists()]
    registry = (ROOT / "operators" / "__init__.py").read_text(encoding="utf-8")
    assert "detail_data_migration_op" not in registry
    assert "bmanga.detail_data_migrate" not in registry


def test_product_code_has_no_pre_52_runtime_branch():
    violations: list[str] = []
    for path in _product_sources():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if "Blender 5.1" in source:
            violations.append(path.relative_to(ROOT).as_posix())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr not in {
                "version",
                "version_string",
            }:
                continue
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "app"
                and isinstance(value.value, ast.Name)
                and value.value.id == "bpy"
            ):
                violations.append(
                    f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
                )
    assert not violations


def test_all_extension_manifests_require_blender_52():
    manifests = (
        ROOT / "blender_manifest.toml",
        ROOT / "addons" / "b_manga_line" / "blender_manifest.toml",
        ROOT / "addons" / "b_manga_render" / "blender_manifest.toml",
    )
    for path in manifests:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        assert payload["blender_version_min"] == "5.2.0"


def test_domain_repository_is_the_only_project_metadata_writer():
    direct_writers: list[str] = []
    for path in _product_sources():
        relative = path.relative_to(ROOT).as_posix()
        if relative in {
            "bmanga_core/domain_repository.py",
            "io/durable_storage.py",
        }:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name not in {
                "json_io.write_json",
                "Path.write_text",
                "path.write_text",
                "atomic_write_json",
            }:
                continue
            rendered = ast.get_source_segment(source, node) or ""
            if "project.json" in rendered or "page.json" in rendered:
                direct_writers.append(f"{relative}:{node.lineno}")
    assert not direct_writers


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def test_domain_load_is_one_way_and_property_callbacks_are_suppressed():
    source = (ROOT / "io" / "domain_projection.py").read_text(encoding="utf-8")
    apply_body = source[
        source.index("def apply_project_document")
        : source.index("def bind_project_document")
    ]
    page_apply_body = source[
        source.index("def apply_page_document")
        : source.index("def _payloads_for_kind")
    ]
    assert "suppress_page_number_range_update" in apply_body
    assert "_suspend_load_property_side_effects" in apply_body
    assert "schema.work_from_dict" in apply_body
    assert "_suspend_load_property_side_effects" in page_apply_body
    assert "_apply_page_owned_collections" in page_apply_body
    work_io = (ROOT / "io" / "work_io.py").read_text(encoding="utf-8")
    page_io = (ROOT / "io" / "page_io.py").read_text(encoding="utf-8")
    assert "repository.load_project()" in work_io
    assert "apply_project_document" in work_io
    assert "repository.load_page(" in page_io
    assert "apply_page_document" in page_io
    assert page_io.index("page_entry.detail_loaded = True") < page_io.index(
        "domain_projection.apply_page_document("
    )


def test_projection_saves_use_explicit_field_patches_only():
    store_source = (ROOT / "bmanga_core" / "domain_store.py").read_text(
        encoding="utf-8"
    )
    assert "class ReplaceProject" not in store_source
    assert "class ReplacePage" not in store_source
    assert "class CommitProjectProjection" not in store_source
    assert "class CommitPageProjection" not in store_source
    assert "class ProjectPatch" in store_source
    assert "class PagePatch" in store_source
    for relative in (
        "io/page_io.py",
        "io/work_io.py",
        "io/page_operation_transaction.py",
        "io/coma_operation_transaction.py",
        "io/coma_move_transaction.py",
        "operators/spread_op.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "ReplaceProject" not in source
        assert "ReplacePage" not in source
        assert "CommitProjectProjection" not in source
        assert "CommitPageProjection" not in source
        assert "ApplyProjectPatch" in source


def test_node_z_order_remains_in_domain_settings():
    source = (ROOT / "io" / "domain_projection.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "_NODE_STRUCTURAL_FIELDS"
            for target in node.targets
        )
    )
    structural_fields = ast.literal_eval(assignment.value)
    assert "zOrder" not in structural_fields


def test_zero_active_indexes_are_not_coerced_to_minus_one():
    guarded_sources = (
        "utils/data_name_organizer.py",
        "operators/coma_renumber_op.py",
        "operators/layer_link_duplicate_op.py",
        "operators/object_rotation_raster.py",
        "panels/gpencil_panel.py",
        "operators/layer_stack_op.py",
    )
    violations = [
        relative
        for relative in guarded_sources
        if " or -1" in (ROOT / relative).read_text(encoding="utf-8")
    ]
    assert not violations


def test_old_layout_names_only_exist_in_explicit_rejection_boundary():
    allowed = {
        "bmanga_core/domain_repository.py",
    }
    violations: list[str] = []
    for path in _product_sources():
        relative = path.relative_to(ROOT).as_posix()
        if relative in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        if '"work.json"' in source or '"pages.json"' in source:
            violations.append(relative)
    assert not violations


def test_all_native_tree_mutations_use_durable_transactions():
    page_operations = (ROOT / "io" / "page_operation_transaction.py").read_text(
        encoding="utf-8"
    )
    spread_operations = (ROOT / "io" / "spread_fs_transaction.py").read_text(
        encoding="utf-8"
    )
    assert page_operations.count(
        "native_tree_transaction.NativeTreeTransaction("
    ) >= 2
    assert "native_tree_transaction.NativeTreeTransaction(" in spread_operations

    coma_sources = {
        relative: (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "operators/coma_op.py",
            "operators/coma_knife_cut_op.py",
            "operators/coma_split_op.py",
        )
    }
    assert coma_sources["operators/coma_op.py"].count(
        "coma_operation_transaction.ComaOperationTransaction("
    ) >= 4
    assert (
        "coma_operation_transaction.ComaOperationTransaction("
        in coma_sources["operators/coma_knife_cut_op.py"]
    )
    assert (
        "coma_operation_transaction.ComaOperationTransaction("
        in coma_sources["operators/coma_split_op.py"]
    )
    forbidden = (
        "coma_io.copy_coma_files(",
        "coma_io.remove_coma_files(",
        "coma_io.move_coma_files(",
    )
    assert not [
        f"{relative}:{name}"
        for relative, source in coma_sources.items()
        for name in forbidden
        if name in source
    ]


def test_native_transaction_recovery_precedes_domain_projection():
    work_io = (ROOT / "io" / "work_io.py").read_text(encoding="utf-8")
    handlers = (ROOT / "utils" / "handlers.py").read_text(encoding="utf-8")
    recovery_call = "recover_pending_native_transactions("
    assert recovery_call in work_io
    assert work_io.index(recovery_call) < work_io.index(
        "repository.load_project()"
    )
    assert recovery_call in handlers
