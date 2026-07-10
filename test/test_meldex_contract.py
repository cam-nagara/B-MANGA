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


if __name__ == "__main__":
    test_contract_keeps_text_and_type_exactly()
    _contract_rejects_wrong_identity("contract", "wrong")
    _contract_rejects_wrong_identity("version", 2)
    test_contract_rejects_duplicate_rows_and_bad_ruby()
    print("BMANGA_MELDEX_CONTRACT_OK")
