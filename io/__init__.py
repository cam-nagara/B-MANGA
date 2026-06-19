"""io — ファイル入出力 (.bmanga / work.json / pages.json / page.blend / cNN.*).

Phase 1-B: work_io / page_io / presets / schema を提供。
Blender への bpy.types 登録は無いが、他モジュールからの import 都合で
register/unregister は空関数として残す。
"""

from __future__ import annotations

# 公開サブモジュール (他層からのインポート都合)
from . import (  # noqa: F401
    blend_io,
    coma_io,
    meldex_receiver,
    page_io,
    presets,
    schema,
    settings_bundle,
    shared_presets,
    work_io,
)


def register() -> None:
    meldex_receiver.register()


def unregister() -> None:
    meldex_receiver.unregister()
