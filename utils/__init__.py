"""Blender API と Python 標準ライブラリのみに依存するヘルパー群。"""

from . import python_deps

python_deps.ensure_bundled_wheels_on_path()

from . import handlers, log, page_grid  # noqa: E402,F401 — page_grid はヘルパのみ
from . import object_naming, outliner_model, layer_object_sync  # noqa: E402,F401
from . import (  # noqa: E402,F401
    active_collection_sync,
    active_target,
    asset_drop_runtime,
    balloon_curve_object,
    balloon_merge_object,
    camera_overview_sync,
    coma_plane,
    cross_addon_settings_sync,
    effect_line_object,
    empty_layer_object,
    fill_real_object,
    geometry_nodes_bridge,
    gp_object_layer,
    mask_apply,
    mask_object,
    outliner_watch,
    paper_bg_object,
    paper_guide_object,
)


def register() -> None:
    log.register()
    handlers.register()
    outliner_watch.register()
    active_collection_sync.register()
    cross_addon_settings_sync.register()
    camera_overview_sync.register()
    asset_drop_runtime.register()
    paper_bg_object.register()
    paper_guide_object.register()
    geometry_nodes_bridge.register()
    coma_plane.register()
    fill_real_object.register()


def unregister() -> None:
    fill_real_object.unregister()
    asset_drop_runtime.unregister()
    coma_plane.unregister()
    geometry_nodes_bridge.unregister()
    paper_guide_object.unregister()
    paper_bg_object.unregister()
    camera_overview_sync.unregister()
    cross_addon_settings_sync.unregister()
    active_collection_sync.unregister()
    outliner_watch.unregister()
    handlers.unregister()
    try:
        layer_object_sync.clear_snapshots()
    except Exception:  # noqa: BLE001
        pass
    log.unregister()
