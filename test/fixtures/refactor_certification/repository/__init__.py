import bpy
from bpy.props import BoolProperty


class BMANGA_OT_fixture_export(bpy.types.Operator):
    bl_idname = "bmanga.fixture_export"
    bl_label = "Fixture Export"


class BMANGA_PT_fixture(bpy.types.Panel):
    bl_idname = "BMANGA_PT_fixture"
    bl_label = "Fixture Panel"
    bl_category = "B-MANGA"


class BMangaFixtureSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="Enabled", default=True)


def _preset_private_helper():
    return None
