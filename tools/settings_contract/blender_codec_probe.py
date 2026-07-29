"""Blender実機characterizationが使う現行JSON codec台帳。"""

from __future__ import annotations

import importlib


DIRECT_JSON_CODECS = {
    "BMangaBalloonEntry": ("io.schema", "balloon_entry_to_dict", "balloon_entry_from_dict"),
    "BMangaComaBorder": ("io.schema", "coma_border_to_dict", "coma_border_from_dict"),
    "BMangaComaEntry": ("io.schema", "coma_entry_to_dict", "coma_entry_from_dict"),
    "BMangaComaGap": ("io.schema", "coma_gap_to_dict", "coma_gap_from_dict"),
    "BMangaComaWhiteMargin": (
        "io.schema",
        "coma_white_margin_to_dict",
        "coma_white_margin_from_dict",
    ),
    "BMangaDisplayItem": ("io.schema", "display_item_to_dict", "display_item_from_dict"),
    "BMangaEffectLineParams": (
        "core.effect_line",
        "effect_params_to_dict",
        "effect_params_from_dict",
    ),
    "BMangaFillLayer": ("io.schema", "fill_layer_to_dict", "fill_layer_from_dict"),
    "BMangaImageLayer": ("io.schema", "image_layer_to_dict", "image_layer_from_dict"),
    "BMangaImagePathLayer": (
        "io.image_path_schema",
        "image_path_layer_to_dict",
        "image_path_layer_from_dict",
    ),
    "BMangaLayerFolder": (
        "io.schema",
        "layer_folder_to_dict",
        "layer_folder_from_dict",
    ),
    "BMangaNombre": ("io.schema", "nombre_to_dict", "nombre_from_dict"),
    "BMangaPageEntry": ("io.schema", "page_entry_to_dict", "page_entry_from_dict"),
    "BMangaPaperSettings": ("io.schema", "paper_to_dict", "paper_from_dict"),
    "BMangaRasterLayer": (
        "io.schema",
        "raster_layer_to_dict",
        "raster_layer_from_dict",
    ),
    "BMangaSafeAreaOverlay": ("io.schema", "safe_area_to_dict", "safe_area_from_dict"),
    "BMangaTextEntry": ("io.schema", "text_entry_to_dict", "text_entry_from_dict"),
    "BMangaWorkData": ("io.schema", "work_to_dict", "work_from_dict"),
    "BMangaWorkInfo": ("io.schema", "work_info_to_dict", "work_info_from_dict"),
}

ADDITIONAL_DIRECT_JSON_CODECS = {
    "BMangaPageEntry": (("io.schema", "page_to_dict", "page_from_dict"),),
}

NESTED_JSON_BINDINGS = {
    "BMangaBalloonShapeParams": "nested:BMangaBalloonEntry.shape_params",
    "BMangaBalloonTail": "nested:BMangaBalloonEntry.tails",
    "BMangaBalloonTailPoint": "nested:BMangaBalloonEntry.tails[].points",
    "BMangaComaVertex": "nested:BMangaComaEntry.vertices",
    "BMangaLayerRef": "nested:BMangaComaEntry.layer_refs",
    "BMangaOriginalPageRef": "nested:BMangaPageEntry.original_pages",
    "BMangaRubySpan": "nested:BMangaTextEntry.ruby_spans",
    "BMangaRubySegment": "nested:BMangaTextEntry.ruby_spans[].segments",
    "BMangaTextFontSpan": "nested:BMangaTextEntry.font_spans",
    "BMangaTextStyleSpan": "nested:BMangaTextEntry.style_spans",
}


def direct_json_codecs(package_name: str, owner_name: str):
    bindings = []
    primary = DIRECT_JSON_CODECS.get(owner_name)
    if primary is not None:
        bindings.append(primary)
    bindings.extend(ADDITIONAL_DIRECT_JSON_CODECS.get(owner_name, ()))
    out = []
    for module_name, encoder_name, decoder_name in bindings:
        module = importlib.import_module(f"{package_name}.{module_name}")
        label = f"{module_name}.{encoder_name}/{module_name}.{decoder_name}"
        out.append(
            (
                label,
                getattr(module, encoder_name),
                getattr(module, decoder_name),
            )
        )
    return tuple(out)


def direct_json_codec(package_name: str, owner_name: str):
    codecs = direct_json_codecs(package_name, owner_name)
    if not codecs:
        return None
    _label, encoder, decoder = codecs[0]
    return encoder, decoder


__all__ = (
    "NESTED_JSON_BINDINGS",
    "direct_json_codec",
    "direct_json_codecs",
)
