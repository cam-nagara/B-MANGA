from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _module():
    path = Path(__file__).resolve().parents[1] / "io" / "meldex_contract.py"
    spec = importlib.util.spec_from_file_location("meldex_contract_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _payload():
    return {
        "contract": "meldex-bmanga-scenario",
        "version": 1,
        "source": {"documentId": "doc-1"},
        "pages": [{"rows": [{"rowId": "r1", "type": "会話", "body": "改行\n本文", "rubies": []}]}],
    }


def test_contract_keeps_text_and_type_exactly():
    document = _module().validate_payload(_payload())
    assert document.pages[0].rows[0].type_name == "会話"
    assert document.pages[0].rows[0].body == "改行\n本文"


def _contract_rejects_wrong_identity(field, value):
    module = _module()
    payload = _payload()
    payload[field] = value
    try:
        module.validate_payload(payload)
    except module.ContractError:
        pass
    else:
        raise AssertionError("wrong contract identity was accepted")


def test_contract_rejects_duplicate_rows_and_bad_ruby():
    module = _module()
    payload = _payload()
    payload["pages"][0]["rows"].append(dict(payload["pages"][0]["rows"][0]))
    try:
        module.validate_payload(payload)
    except module.ContractError:
        pass
    else:
        raise AssertionError("duplicate row id was accepted")
    payload = _payload()
    payload["pages"][0]["rows"][0]["rubies"] = [{"start": 99, "length": 1, "rubyText": "x"}]
    try:
        module.validate_payload(payload)
    except module.ContractError:
        pass
    else:
        raise AssertionError("out-of-range ruby was accepted")


def test_contract_v2_keeps_presentation_and_segments():
    payload = _payload()
    payload["version"] = 2
    payload["indexUnit"] = "unicode-code-point"
    payload["normalization"] = "none"
    payload["presentation"] = {"ruby": {
        "writingMode": "horizontal", "sizePercent": 75, "gapEm": 0.2,
        "letterSpacingEm": 0.1, "lineHeight": 1.8, "align": "start",
        "smallKana": "keep", "fontPreset": "inherit",
    }}
    payload["pages"][0]["rows"][0]["body"] = "東京"
    payload["pages"][0]["rows"][0]["rubies"] = [{
        "start": 0, "length": 2, "rubyText": "とうきょう", "style": "jukugo",
        "origin": "manual", "priority": 10,
        "segments": [
            {"start": 0, "length": 1, "rubyText": "とう"},
            {"start": 1, "length": 1, "rubyText": "きょう"},
        ],
    }]
    document = _module().validate_payload(payload)
    assert document.version == 2
    assert document.presentation["ruby"]["gapEm"] == 0.2
    assert document.pages[0].rows[0].rubies[0]["segments"][1]["rubyText"] == "きょう"


def test_contract_v2_rejects_os_font_path():
    module = _module()
    payload = _payload()
    payload["version"] = 2
    payload["indexUnit"] = "unicode-code-point"
    payload["normalization"] = "none"
    payload["presentation"] = {"ruby": {"fontPreset": r"C:\\Windows\\Fonts\\foo.ttf"}}
    try:
        module.validate_payload(payload)
    except module.ContractError:
        pass
    else:
        raise AssertionError("OS font path was accepted as a logical preset")


if __name__ == "__main__":
    test_contract_keeps_text_and_type_exactly()
    _contract_rejects_wrong_identity("contract", "wrong")
    _contract_rejects_wrong_identity("version", 3)
    test_contract_rejects_duplicate_rows_and_bad_ruby()
    test_contract_v2_keeps_presentation_and_segments()
    test_contract_v2_rejects_os_font_path()
    print("BMANGA_MELDEX_CONTRACT_OK")
