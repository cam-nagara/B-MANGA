"""線幅グラフ (線幅グラフ・白線幅グラフ・黒線幅グラフ) の「適用」ボタン専用 Operator.

線幅グラフはドラッグのたびにメッシュを再生成すると重いため、常駐タイマー
(``utils/effect_inout_curve.py``) はグラフの表示更新だけを行い、編集内容の
パラメータへの確定は行わない。このOperatorがその確定を一括で行う唯一の
経路 (詳細設定のOK確定を除く) になる。
"""

from __future__ import annotations

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from ..utils import effect_inout_curve, layer_stack
from . import detail_dialog_runtime


class BMANGA_OT_effect_profile_graph_apply(Operator):
    """線幅グラフの編集内容を確定し、メッシュを1回だけ再生成する."""

    bl_idname = "bmanga.effect_profile_graph_apply"
    bl_label = "線幅グラフを適用"
    bl_description = "線幅グラフの編集内容を確定してメッシュを再生成します"
    bl_options = {"REGISTER", "UNDO"}

    profile_key: EnumProperty(
        name="対象グラフ",
        items=(
            ("main", "線幅グラフ", "効果線・ウニフラの線幅グラフ"),
            ("white", "白線幅グラフ", "白抜き線の白線幅グラフ"),
            ("black", "黒線幅グラフ", "白抜き線の黒線幅グラフ"),
        ),
        default="main",
    )

    def execute(self, context):
        fields, node_name, source_prop, label = effect_inout_curve.profile_spec_for_key(
            self.profile_key
        )
        params = self._resolve_params(context, self.profile_key, fields)
        node = effect_inout_curve.get_profile_node(node_name)
        if params is None or node is None:
            self.report({"WARNING"}, f"適用できる{label}が見つかりません")
            return {"CANCELLED"}
        effect_inout_curve.commit_profile_node_to_params(
            params,
            fields=fields,
            node_name=node_name,
            source_prop=source_prop,
        )
        self.report({"INFO"}, f"{label}を適用しました")
        layer_stack.tag_view3d_redraw(context)
        return {"FINISHED"}

    @staticmethod
    def _resolve_params(context, profile_key, fields):
        """確定先パラメータを解決する。

        (a) 詳細設定 (効果線・フキダシ・画像パス) が同じSceneで開いていれば
        その固定対象を優先する。(b) 開いていなければ効果線ツールパネルの
        ``scene.bmanga_effect_line_params`` を対象にする (パネル上のグラフを
        直接編集しているケース)。どちらも該当フィールドを持たなければ
        ``None``。
        """
        params = detail_dialog_runtime.active_curve_params_for_scene(
            context, profile_key=profile_key
        )
        if params is not None:
            return params
        scene_params = getattr(context.scene, "bmanga_effect_line_params", None)
        if scene_params is not None and (
            fields is None or all(hasattr(scene_params, attr) for attr in fields.values())
        ):
            return scene_params
        return None


_CLASSES = (BMANGA_OT_effect_profile_graph_apply,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
