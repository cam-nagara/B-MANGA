"""連続実行アプリ ロジック層の単体テスト（Blender 非依存・標準 python）。

実行（リポジトリ直下に addon の __init__.py があり pytest が巻き込むため、
test ディレクトリ内から起動する）:
    cd test && python -m pytest render_batch_logic_test.py --import-mode=importlib
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_PARENT = ROOT / "tools" / "render_batch"
if str(APP_PARENT) not in sys.path:
    sys.path.insert(0, str(APP_PARENT))

from app import model  # noqa: E402
from app.jobstore import JobStore  # noqa: E402
from app.model import Job  # noqa: E402
from app.predictor import Predictor, project_finish_times  # noqa: E402


@pytest.fixture
def store(tmp_path):
    # 同期猶予なしでテスト高速化。
    return JobStore(str(tmp_path / "shared"), sync_grace_seconds=0.0)


def _job(blend, preset, **kw):
    return Job(blend_path=blend, preset_name=preset, **kw)


# ---- キュー基本 ----
def test_add_list_and_order(store):
    a = store.add_job(_job("//x/a.blend", "キャラ"))
    b = store.add_job(_job("//x/b.blend", "背景"))
    jobs = store.list_jobs()
    assert [j.id for j in jobs] == [a.id, b.id]
    assert jobs[0].order < jobs[1].order


def test_reorder(store):
    a = store.add_job(_job("//x/a.blend", "P1"))
    b = store.add_job(_job("//x/b.blend", "P2"))
    c = store.add_job(_job("//x/c.blend", "P3"))
    store.reorder([c.id, a.id, b.id])
    assert [j.id for j in store.list_jobs()] == [c.id, a.id, b.id]


def test_remove(store):
    a = store.add_job(_job("//x/a.blend", "P1"))
    store.remove_job(a.id)
    assert store.list_jobs() == []


# ---- claim（実行権取得）----
def test_claim_sets_running(store):
    a = store.add_job(_job("//x/a.blend", "P1"))
    assert store.claim(a.id, "PC-A") is True
    job = store.get_job(a.id)
    assert job.status == model.STATUS_RUNNING
    assert job.claimed_by == "PC-A"


def test_claim_winner_is_deterministic(store):
    """2台が同時に同じジョブを取りに行っても、勝者は1台だけ（辞書順最小）。

    同時競合を模すため、claim 判定前に両PCの宣言ファイルを置いてから判定する。
    """
    a = store.add_job(_job("//x/a.blend", "P1"))
    # PC-B が先に宣言ファイルを置いた状態を作る（まだ running 化していない）。
    store._atomic_write(store.claim_dir / f"{a.id}__PC-B.json",
                        {"job_id": a.id, "pc": "PC-B", "at": model.now_iso()})
    # この状態で PC-A が claim すると、辞書順最小の PC-A が勝つ。
    assert store.claim(a.id, "PC-A") is True
    assert store.get_job(a.id).claimed_by == "PC-A"
    # 既に running なので PC-B の後追い claim は失敗する。
    assert store.claim(a.id, "PC-B") is False


def test_claim_single_claimant_wins(store):
    """単独で宣言したPCはそのまま勝つ。"""
    a = store.add_job(_job("//x/a.blend", "P1"))
    assert store.claim(a.id, "PC-B") is True
    assert store.get_job(a.id).claimed_by == "PC-B"


def test_find_next_respects_target_pc(store):
    store.add_job(_job("//x/a.blend", "P1", target_pc="PC-Z", order=10))
    b = store.add_job(_job("//x/b.blend", "P2", order=20))
    nxt = store.find_next_for("PC-A")
    assert nxt.id == b.id  # PC-Z 指定のジョブは PC-A には回らない


# ---- 完了・失敗・再投入 ----
def test_complete_records_timing_and_history(store):
    a = store.add_job(_job("//x/a.blend", "キャラ"))
    store.claim(a.id, "PC-A")
    timing = {
        "elapsed_seconds": 120.0,
        "exec_count": 3,
        "resolution": [2480, 3508],
        "renders": [{"label": "パス", "samples": 64, "elapsed_seconds": 80.0}],
        "started_at": "2026-05-29T10:00:00+09:00",
    }
    store.complete(store.get_job(a.id), timing)
    job = store.get_job(a.id)
    assert job.status == model.STATUS_DONE
    assert job.elapsed_seconds == 120.0
    assert job.resolution == [2480, 3508]
    hist = store.read_history()
    assert len(hist) == 1 and hist[0].elapsed_seconds == 120.0


def test_fail_then_requeue(store):
    a = store.add_job(_job("//x/a.blend", "P1"))
    store.claim(a.id, "PC-A")
    store.fail(store.get_job(a.id), "boom")
    assert store.get_job(a.id).status == model.STATUS_ERROR
    store.requeue(a.id)
    job = store.get_job(a.id)
    assert job.status == model.STATUS_QUEUED
    assert job.claimed_by == "" and job.error == ""


# ---- 予測 ----
def _done(blend, preset, secs, res, samples, pc="PC-A"):
    return Job(
        blend_path=blend, preset_name=preset, status=model.STATUS_DONE,
        elapsed_seconds=secs, resolution=res, claimed_by=pc,
        renders=[{"samples": samples}],
    )


def test_predict_exact_condition_average():
    hist = [
        _done("//x/a.blend", "キャラ", 100, [100, 100], 64),
        _done("//x/a.blend", "キャラ", 140, [100, 100], 64),
    ]
    p = Predictor(hist)
    secs, why = p.predict(_done("//x/a.blend", "キャラ", 0, [100, 100], 64))
    assert secs == 120.0
    assert "同一条件" in why


def test_predict_falls_back_to_preset_only():
    hist = [_done("//x/a.blend", "キャラ", 200, [100, 100], 64)]
    p = Predictor(hist)
    # 別ファイル・別解像度 → preset のみに緩和してヒット。
    secs, why = p.predict(_done("//y/b.blend", "キャラ", 0, [999, 999], 1))
    assert secs == 200.0
    assert "同プリセット" in why


def test_predict_unknown_returns_zero():
    p = Predictor([])
    secs, why = p.predict(_done("//x/a.blend", "未知", 0, [100, 100], 64))
    assert secs == 0.0 and why == "不明"


def test_project_finish_times_accumulates_per_lane():
    hist = [_done("//x/a.blend", "P", 100, [10, 10], 1)]
    p = Predictor(hist)
    jobs = [
        Job(id="j1", blend_path="//x/a.blend", preset_name="P", resolution=[10, 10], renders=[{"samples": 1}]),
        Job(id="j2", blend_path="//x/a.blend", preset_name="P", resolution=[10, 10], renders=[{"samples": 1}]),
    ]
    finish = project_finish_times(jobs, p, now_epoch=1000.0)
    # 同一レーン（target_pc 空）なので累積: j1=1100, j2=1200。
    assert finish["j1"] == 1100.0
    assert finish["j2"] == 1200.0
