"""フキダシ描画の共通契約.

フキダシは、塗り、外側フチ、内側フチ、多重線、主線を同じ基準形状から作る。
素材スロット、役割番号、前後関係はこのモジュールだけを正にして、個別モジュール
で重複定義しない。

Phase D 以降、Geometry Nodes 経由の描画は撤去され、Python メッシュ
(balloon_fill_mesh / balloon_line_mesh) で全描画責務を担う。 役割番号
(`*_ROLE_RADIUS`) は旧データ (古い .blend のスプライン) のクリーンアップで
引き続き参照される。
"""

from __future__ import annotations

MATERIAL_SLOT_FILL = 0
MATERIAL_SLOT_OUTER_EDGE = 1
MATERIAL_SLOT_INNER_EDGE = 2
MATERIAL_SLOT_LINE = 3

MULTI_LINE_ROLE_RADIUS_OFFSET = 100.0
OUTER_EDGE_ROLE_RADIUS = 200.0
INNER_EDGE_ROLE_RADIUS = 300.0
CLIPPED_FILL_ROLE_RADIUS = 400.0
MAIN_LINE_FILL_ROLE_RADIUS = 500.0

LINE_AND_EDGE_MASK_POWER = 4.0

FILL_Z_M = 0.0
OUTER_EDGE_Z_M = 0.000020
INNER_EDGE_Z_M = 0.000040
MULTI_LINE_Z_M = 0.000080
LINE_Z_M = 0.000100
