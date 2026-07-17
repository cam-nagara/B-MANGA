"""keymap — B-MANGA 専用キーマップとビューポート操作モーダルオペレータ.

register 順序は 5.3 に従い最後。依存する Preferences / utils.log は先に
register 済みである前提。
"""

from . import os_compat  # noqa: F401 - 他モジュールからの参照用
from . import viewport_ops
from . import keymap as _keymap
from . import startup_repair


def register() -> None:
    viewport_ops.register()
    _keymap.register()
    # register 時点では userpref.blend のキーマップカスタマイズ (過去に
    # 無効化されたまま焼き付いた標準キー) がまだ適用されていないことが
    # あるため、起動後に自己修復を再実行するタイマーを登録する。
    startup_repair.register()


def unregister() -> None:
    startup_repair.unregister()
    _keymap.unregister()
    viewport_ops.unregister()
