import bpy


SUPPORTED_FORMATS = ("PNG", "JPEG", "TIFF", "PSD", "PDF")


class BMANGA_RENDER_OT_fixture_export(bpy.types.Operator):
    bl_idname = "bmanga_render.export_fixture"
    bl_label = "Fixture Export"
