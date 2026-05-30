"""効果線の始点コマ枠が「効果線自身のページ」のコマから解決されることを確認.

以前は ``_start_frame_outline_for_bounds`` が ``get_active_page`` を使っており、
「カーソル追従でアクティブページ切替」などでアクティブページが別ページに
なっている瞬間に効果線を作ると、始点コマ枠が別ページのコマ形状になり
「別のページのコマのマスクが適用される」症状が出ていた。効果線自身の
parent_key を正としてページを解決することを保証する。
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]


def _load_addon():
    spec = importlib.util.spec_from_file_location(
        "bname_dev_effect_start_page",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bname_dev_effect_start_page"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    mod.register()
    return mod


class _FakeObj:
    def __init__(self):
        self._d = {}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __setitem__(self, key, value):
        self._d[key] = value


def main() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="bname_effect_start_page_"))
    mod = None
    try:
        mod = _load_addon()
        bpy.context.scene.bname_overview_mode = True
        if "FINISHED" not in bpy.ops.bname.work_new(
            filepath=str(temp_root / "EffectStartPage.bname")
        ):
            raise AssertionError("作品作成に失敗しました")

        from bname_dev_effect_start_page.core.work import get_active_page, get_work
        from bname_dev_effect_start_page.operators import effect_line_op
        from bname_dev_effect_start_page.utils import object_naming as on

        work = get_work(bpy.context)
        if "FINISHED" not in bpy.ops.bname.page_duplicate():
            raise AssertionError("ページ複製に失敗しました")
        if len(work.pages) < 2:
            raise AssertionError("複製後にページが2つありません")

        page0_id = str(work.pages[0].id)
        page1_id = str(work.pages[1].id)

        # アクティブページを page0 にしておく (= 別ページ)。
        work.active_page_index = 0
        active = get_active_page(bpy.context)
        if active is None or str(active.id) != page0_id:
            raise AssertionError("アクティブページの設定に失敗しました")

        # parent_key が page1 の効果線は、アクティブ (page0) ではなく
        # 自身のページ (page1) から解決されなければならない。
        obj = _FakeObj()
        obj[on.PROP_PARENT_KEY] = f"{page1_id}:c01"
        resolved = effect_line_op._page_for_effect_object(bpy.context, obj)
        if resolved is None or str(resolved.id) != page1_id:
            raise AssertionError(
                "効果線の始点コマ枠が自身のページから解決されていません "
                f"(parent={page1_id} なのに {getattr(resolved, 'id', None)} を返した "
                "= 別ページのコマ枠/マスクが適用される)"
            )

        # parent_key が空ならアクティブページへフォールバックする。
        obj2 = _FakeObj()
        obj2[on.PROP_PARENT_KEY] = ""
        fallback = effect_line_op._page_for_effect_object(bpy.context, obj2)
        if fallback is None or str(fallback.id) != page0_id:
            raise AssertionError("parent_key 空のときアクティブページへフォールバックしていません")

        print("BNAME_EFFECT_START_FRAME_PAGE_OK", flush=True)
    finally:
        if mod is not None:
            mod.unregister()
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
