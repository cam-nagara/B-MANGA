"""所要時間の予測（条件別の平均）。

完了記録（history）から、条件キーごとの平均所要時間を求める。
予測対象に完全一致する記録が無ければ、条件を段階的に緩めて推定する。

条件キー（細→粗）:
  1) preset + blend(ファイル名) + 解像度 + サンプル数合計 + pc
  2) preset + blend(ファイル名) + 解像度 + サンプル数合計
  3) preset + blend(ファイル名)
  4) preset

実行待ち(queued)ジョブは解像度・サンプル数が未確定（実行後に埋まる）のため、
同じ preset+ファイルの過去記録からそれらを補完してから L1/L2 を判定する。
補完しないと queued ジョブは解像度/サンプル別の精緻な平均に決して一致せず、
常に L3/L4 の粗い推定に落ちてしまう。
"""

from __future__ import annotations

from copy import copy
from datetime import datetime

from .model import Job, STATUS_DONE, STATUS_RUNNING


def _blend_key(blend_path: str) -> str:
    """ファイル名部分を小文字で返す。

    Windows/UNC/forward-slash いずれの区切りでも末尾要素を取る
    （os.path.basename は ``//x/a`` のような擬似UNCで先頭を残すため使わない）。
    """
    s = str(blend_path or "").replace("\\", "/")
    return s.rsplit("/", 1)[-1].lower()


def _resolution_key(job: Job) -> tuple:
    res = job.resolution or []
    try:
        return (int(res[0]), int(res[1]))
    except (IndexError, TypeError, ValueError):
        return (0, 0)


def _samples_total(job: Job) -> int:
    return sum(int(r.get("samples", 0) or 0) for r in (job.renders or []))


def _signature(job: Job, level: int, pc: str = "") -> tuple:
    blend = _blend_key(job.blend_path)
    samples = _samples_total(job)
    resolution = _resolution_key(job)
    use_pc = pc or job.claimed_by
    if level == 1:
        return ("L1", job.preset_name, blend, resolution, samples, use_pc)
    if level == 2:
        return ("L2", job.preset_name, blend, resolution, samples)
    if level == 3:
        return ("L3", job.preset_name, blend)
    return ("L4", job.preset_name)


def _iso_to_epoch(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except (ValueError, TypeError):
        return 0.0


class Predictor:
    """history の完了記録から条件別平均を作り、予測を返す。"""

    def __init__(self, history: list[Job]):
        # 正常完了かつ所要時間>0 のものだけを学習に使う。
        self.samples = [j for j in history if j.status == STATUS_DONE and j.elapsed_seconds > 0]
        self._buckets: dict[tuple, list[float]] = {}
        # (preset, ファイル名) -> 代表(解像度, サンプル数)。queued ジョブの補完用。
        self._profile: dict[tuple, tuple] = {}
        for job in self.samples:
            for level in (1, 2, 3, 4):
                self._buckets.setdefault(_signature(job, level), []).append(job.elapsed_seconds)
            res = _resolution_key(job)
            samp = _samples_total(job)
            if res != (0, 0) or samp:
                self._profile[(job.preset_name, _blend_key(job.blend_path))] = (res, samp)

    def _effective(self, job: Job) -> Job:
        """解像度/サンプルが未確定（queued）なら、同条件の過去記録から補完する。"""
        res = _resolution_key(job)
        samp = _samples_total(job)
        if res != (0, 0) and samp:
            return job
        prof = self._profile.get((job.preset_name, _blend_key(job.blend_path)))
        if not prof:
            return job
        eff = copy(job)
        if res == (0, 0) and prof[0] != (0, 0):
            eff.resolution = [prof[0][0], prof[0][1]]
        if not samp and prof[1]:
            eff.renders = [{"samples": prof[1]}]
        return eff

    def predict(self, job: Job, pc: str = "") -> tuple[float, str]:
        """(予測秒, 根拠ラベル) を返す。根拠が無ければ (0.0, "不明")。"""
        labels = {1: "同一条件", 2: "同解像度/サンプル", 3: "同ファイル", 4: "同プリセット"}
        eff = self._effective(job)
        for level in (1, 2, 3, 4):
            values = self._buckets.get(_signature(eff, level, pc))
            if values:
                avg = sum(values) / len(values)
                return round(avg, 1), f"{labels[level]}({len(values)}件平均)"
        return 0.0, "不明"

    def predict_seconds(self, job: Job, pc: str = "") -> float:
        return self.predict(job, pc)[0]


def project_finish_times(jobs: list[Job], predictor: "Predictor", now_epoch: float) -> dict[str, float]:
    """各PCのレーンごとに、待ち行列の累積から完了予測時刻(epoch)を返す。

    1PC1本ずつ前提。target_pc 指定があればそのレーン、空なら "*" レーンに積む。
    予測には target_pc を渡し（PC別実績を反映）、実行中(running)ジョブは既に
    経過した分を差し引いて残り時間で予測する。
    返り値: {job_id: 完了予測epoch}。予測不能なジョブは含めない。
    """
    lane_cursor: dict[str, float] = {}
    result: dict[str, float] = {}
    for job in jobs:
        secs = predictor.predict_seconds(job, job.target_pc)
        if secs <= 0:
            continue
        lane = job.target_pc or "*"
        if job.status == STATUS_RUNNING and job.started_at:
            elapsed = max(0.0, now_epoch - _iso_to_epoch(job.started_at))
            remaining = max(0.0, secs - elapsed)
            start = max(lane_cursor.get(lane, now_epoch), now_epoch)
            finish = start + remaining
        else:
            start = lane_cursor.get(lane, now_epoch)
            finish = start + secs
        lane_cursor[lane] = finish
        result[job.id] = finish
    return result
