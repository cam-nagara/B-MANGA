def register_shortcut(km):
    return km.keymap_items.new(
        "bmanga.fixture_export", type="E", value="PRESS", ctrl=True
    )


def register_wrapped_shortcut(_add):
    _add("bmanga.fixture_export", "F", shift=True)
