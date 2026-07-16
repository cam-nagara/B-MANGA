"""作品ファイルからMeldexシナリオ保存ファイルを読み込むOperator."""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

from ..core.work import get_work
from ..io import meldex_scenario_file, meldex_scenario_import
from ..io.meldex_contract import ContractError
from ..utils import log, page_file_scene


_logger = log.get_logger(__name__)


class BMANGA_OT_meldex_scenario_file_import(Operator, ImportHelper):
    """Meldexの保存済みシナリオを現在のB-MANGA作品へ読み込む."""

    bl_idname = "bmanga.meldex_scenario_file_import"
    bl_label = "Meldexシナリオを選択"
    bl_description = "Meldex Scenarioの保存ファイルを読み込み、ページ・フキダシ・テキストへ反映します"
    bl_options = {"REGISTER"}

    filename_ext = ".mel-scenario"
    filter_glob: StringProperty(  # type: ignore[valid-type]
        default="*.mel-scenario;*.scriptnote.json",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        work = get_work(context)
        return bool(
            work is not None
            and work.loaded
            and page_file_scene.is_work_list_scene(context.scene)
        )

    def invoke(self, context, event):
        self.filepath = ""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        work = get_work(context)
        if work is None or not work.loaded:
            self.report({"ERROR"}, "先にB-MANGA作品を開いてください")
            return {"CANCELLED"}
        if not page_file_scene.is_work_list_scene(context.scene):
            self.report({"ERROR"}, "Meldexシナリオは作品ファイルで読み込んでください")
            return {"CANCELLED"}
        path = Path(self.filepath)
        try:
            payload = meldex_scenario_file.load_contract_payload(path)
            result = meldex_scenario_import.import_payload(context, work, payload)
        except (meldex_scenario_file.ScenarioFileError, ContractError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except Exception:  # noqa: BLE001 - 想定外時はログへ詳細を残し、作品を閉じない
            _logger.exception("Meldex scenario file import failed: %s", path)
            self.report({"ERROR"}, "Meldexシナリオの読み込みに失敗しました。ログを確認してください")
            return {"CANCELLED"}
        if context.area is not None:
            context.area.tag_redraw()
        self.report(
            {"INFO"},
            "Meldexシナリオを読み込みました "
            f"（追加ページ{result['pagesAdded']}、新規{result['created']}、更新{result['updated']}）",
        )
        return {"FINISHED"}


_CLASSES = (BMANGA_OT_meldex_scenario_file_import,)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
