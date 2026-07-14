"""詳細対象橋渡しと汎用キャンセル復元のBlender非依存テスト。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "_bmanga_detail_bridge_test"


def _load_modules():
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules.setdefault(PACKAGE, package)
    utils = types.ModuleType(f"{PACKAGE}.utils")
    utils.__path__ = [str(ROOT / "utils")]
    sys.modules.setdefault(f"{PACKAGE}.utils", utils)
    loaded = {}
    for short_name in (
        "detail_dialog",
        "detail_dialog_state",
        "detail_target_resolver",
        "detail_state_adapters",
    ):
        full_name = f"{PACKAGE}.utils.{short_name}"
        spec = importlib.util.spec_from_file_location(full_name, ROOT / "utils" / f"{short_name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        loaded[short_name] = module
    return loaded


MODULES = _load_modules()
dialog = MODULES["detail_dialog"]
dialog_state = MODULES["detail_dialog_state"]
resolver = MODULES["detail_target_resolver"]
adapters = MODULES["detail_state_adapters"]


class FakeProperty:
    def __init__(self, identifier, prop_type="STRING", *, readonly=False):
        self.identifier = identifier
        self.type = prop_type
        self.is_readonly = readonly


class FakeRNA:
    def __init__(self, properties):
        self.properties = tuple(properties)


class FakeCollection(list):
    def __init__(self, factory, values=()):
        super().__init__(values)
        self.factory = factory

    def add(self):
        value = self.factory()
        self.append(value)
        return value

    def clear(self):
        del self[:]


class FakeIDPropertyGroup:
    def __init__(self, values=()):
        self._values = dict(values)

    def keys(self):
        return self._values.keys()

    def get(self, key, default=None):
        return self._values.get(key, default)

    def __setitem__(self, key, value):
        self._values[key] = value

    def __delitem__(self, key):
        del self._values[key]

    def to_dict(self):
        return dict(self._values)


class FakeGroup:
    __detail_property_group__ = True

    def __init__(self, title="", amount=0):
        self.title = title
        self.amount = amount
        self.bl_rna = FakeRNA((FakeProperty("title"), FakeProperty("amount", "INT")))
        self._custom = {}

    def keys(self):
        return self._custom.keys()

    def get(self, key, default=None):
        return self._custom.get(key, default)

    def __setitem__(self, key, value):
        self._custom[key] = value

    def __delitem__(self, key):
        del self._custom[key]


class FakeRoot(FakeGroup):
    def __init__(self):
        super().__init__("original", 7)
        self.child = FakeGroup("child", 2)
        self.items = FakeCollection(FakeGroup, (FakeGroup("one", 1), FakeGroup("two", 2)))
        self.external = object()
        self.bl_rna = FakeRNA(
            (
                FakeProperty("title"),
                FakeProperty("amount", "INT"),
                FakeProperty("child", "POINTER"),
                FakeProperty("items", "COLLECTION"),
                FakeProperty("external", "POINTER"),
            )
        )


class FakeObject:
    def __init__(self):
        self.name = "original object"
        self.hide_viewport = False
        self.hide_render = False
        self.hide_select = False
        self.location = (1.0, 2.0, 3.0)
        self.rotation_mode = "XYZ"
        self.rotation_euler = (0.0, 0.0, 0.5)
        self.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        self.scale = (1.0, 1.0, 1.0)
        self._custom = {"bmanga_id": "image_1", "bmanga_title": "元"}
        self._selected = True

    def keys(self):
        return self._custom.keys()

    def get(self, key, default=None):
        return self._custom.get(key, default)

    def __setitem__(self, key, value):
        self._custom[key] = value

    def __delitem__(self, key):
        del self._custom[key]

    def select_get(self):
        return self._selected

    def select_set(self, value):
        self._selected = bool(value)


class DetailTargetStateBridgeTests(unittest.TestCase):
    def test_pointer_derived_uid_variants_are_rejected(self):
        self.assertTrue(resolver.is_pointer_derived_uid("ptr_7ffabc"))
        self.assertTrue(resolver.is_pointer_derived_uid("gp:ptr_7ffabc"))
        self.assertTrue(resolver.is_pointer_derived_uid("effect:ptr:7ffabc"))
        self.assertFalse(resolver.is_pointer_derived_uid("gp:gp_012345abcdef"))

    def test_recursive_rna_snapshot_restores_values_collections_and_custom_properties(self):
        root = FakeRoot()
        root["saved"] = {"nested": [1, 2]}
        state = adapters.snapshot_rna_state(root)
        root.title = "changed"
        root.child.amount = 99
        root.items.clear()
        root.items.add().title = "new"
        root["saved"] = {"nested": [9]}
        root["temporary"] = True
        adapters.restore_rna_state(root, state)
        self.assertEqual(root.title, "original")
        self.assertEqual(root.child.amount, 2)
        self.assertEqual([(item.title, item.amount) for item in root.items], [("one", 1), ("two", 2)])
        self.assertEqual(root.get("saved"), {"nested": [1, 2]})
        self.assertNotIn("temporary", root.keys())

    def test_existing_custom_property_group_is_restored_in_place(self):
        root = FakeRoot()
        group = FakeIDPropertyGroup({"nested": [1, 2], "remove": True})
        root["saved"] = group
        state = adapters.snapshot_rna_state(root)
        group["nested"] = [9]
        group["temporary"] = True
        adapters.restore_rna_state(root, state)
        self.assertIs(root.get("saved"), group)
        self.assertEqual(group.get("nested"), [1, 2])
        self.assertTrue(group.get("remove"))
        self.assertNotIn("temporary", group.keys())

    def test_actual_registry_restores_entry_and_managed_object_metadata(self):
        data = FakeRoot()
        obj = FakeObject()
        target = dialog.DetailTarget("image", "image_1", "image:image_1", data, object_ref=obj)
        registry = adapters.make_actual_detail_state_registry()
        snapshot = dialog_state.snapshot_detail_state(target, registry=registry)
        data.title = "edited"
        obj.name = "edited object"
        obj.location = (9.0, 9.0, 9.0)
        obj["bmanga_title"] = "変更"
        obj["bmanga_extra"] = "remove"
        obj.select_set(False)
        dialog_state.restore_detail_state(target, snapshot)
        self.assertEqual(data.title, "original")
        self.assertEqual(obj.name, "original object")
        self.assertEqual(obj.location, (1.0, 2.0, 3.0))
        self.assertEqual(obj.get("bmanga_title"), "元")
        self.assertNotIn("bmanga_extra", obj.keys())
        self.assertTrue(obj.select_get())


if __name__ == "__main__":
    unittest.main()
