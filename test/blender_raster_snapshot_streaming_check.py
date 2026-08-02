"""Blender 5.2実機: B4 300/600dpi複数ラスターを定量メモリで退避する。"""

from __future__ import annotations

from array import array
import ctypes
from pathlib import Path
import shutil
import sys
import tempfile
import threading

import bpy


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bmanga_raster_snapshot_streaming"
SENTINEL = "BMANGA_RASTER_SNAPSHOT_STREAMING_OK"
MIB = 1024 * 1024
MAX_RSS_GROWTH = 256 * MIB


def _load_addon_modules():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
_PSAPI = ctypes.WinDLL("psapi", use_last_error=True)
_KERNEL32.GetCurrentProcess.restype = ctypes.c_void_p
_PSAPI.GetProcessMemoryInfo.argtypes = (
    ctypes.c_void_p,
    ctypes.POINTER(_ProcessMemoryCountersEx),
    ctypes.c_ulong,
)
_PSAPI.GetProcessMemoryInfo.restype = ctypes.c_int


def _working_set_bytes() -> int:
    counters = _ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    ok = _PSAPI.GetProcessMemoryInfo(
        _KERNEL32.GetCurrentProcess(),
        ctypes.byref(counters),
        counters.cb,
    )
    if not ok:
        raise ctypes.WinError()
    return int(counters.WorkingSetSize)


class _MemoryMonitor:
    def __init__(self):
        self.baseline = _working_set_bytes()
        self.maximum = self.baseline
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(0.002):
            self.maximum = max(self.maximum, _working_set_bytes())

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.maximum = max(self.maximum, _working_set_bytes())
        self._stop.set()
        self._thread.join(timeout=5.0)

    @property
    def growth(self) -> int:
        return self.maximum - self.baseline


class _VirtualReadPixels:
    """全量読取を拒否し、要求された固定sliceだけを生成する。"""

    def __init__(self, total_values: int, value: float, *, fail=False):
        self.total_values = total_values
        self.value = value
        self.fail = fail
        self.slice_count = 0
        self.max_slice = 0

    def __len__(self) -> int:
        return self.total_values

    def __getitem__(self, key):
        assert isinstance(key, slice)
        assert key.step in {None, 1}
        assert key.start is not None and key.stop is not None
        count = int(key.stop) - int(key.start)
        assert 0 < count <= 262_144, count
        self.slice_count += 1
        self.max_slice = max(self.max_slice, count)
        if self.fail:
            raise OSError("injected second B4 raster snapshot failure")
        return array("f", [self.value]) * count


class _VirtualWritePixels:
    """復元側も固定slice以外を拒否し、全画素を保持せず検査する。"""

    def __init__(self, total_values: int, expected: float):
        self.total_values = total_values
        self.expected = expected
        self.written = 0
        self.max_slice = 0

    def __len__(self) -> int:
        return self.total_values

    def __setitem__(self, key, values) -> None:
        assert isinstance(key, slice)
        assert key.step in {None, 1}
        assert key.start == self.written
        count = int(key.stop) - int(key.start)
        assert count == len(values)
        assert 0 < count <= 262_144, count
        assert abs(float(values[0]) - self.expected) < 1.0e-7
        assert abs(float(values[-1]) - self.expected) < 1.0e-7
        self.written += count
        self.max_slice = max(self.max_slice, count)


class _VirtualImage:
    def __init__(self, width: int, height: int, pixels):
        self.size = (width, height)
        self.pixels = pixels


def _dimensions(dpi: int) -> tuple[int, int]:
    return (
        round(250.0 / 25.4 * dpi),
        round(353.0 / 25.4 * dpi),
    )


def _capture(
    raster_layer_op,
    native_checkpoint_runtime,
    image,
    directory: Path,
    raster_id: str,
):
    return raster_layer_op._capture_raster_pixel_snapshot(
        image,
        native_checkpoint_runtime.snapshot_path(directory, raster_id),
    )


def _exercise_resolution(
    temp_root: Path,
    dpi: int,
    raster_layer_op,
    handlers,
    native_checkpoint_runtime,
) -> int:
    work_dir = temp_root / f"B4-{dpi}.bmanga"
    work_dir.mkdir()
    width, height = _dimensions(dpi)
    total_values = width * height * 4
    values = (0.125, 0.625)
    transaction_id = f"20260731T000000Z-{dpi:012x}"

    # 1枚目を退避した後、2枚目が失敗する場合も中間成果を全清掃する。
    failed_dir = native_checkpoint_runtime.create_snapshot_transaction(
        work_dir,
        transaction_id,
    )
    first = _capture(
        raster_layer_op,
        native_checkpoint_runtime,
        _VirtualImage(
            width,
            height,
            _VirtualReadPixels(total_values, values[0]),
        ),
        failed_dir,
        "raster-first",
    )
    assert first.compressed_path.is_file()
    try:
        _capture(
            raster_layer_op,
            native_checkpoint_runtime,
            _VirtualImage(
                width,
                height,
                _VirtualReadPixels(total_values, values[1], fail=True),
            ),
            failed_dir,
            "raster-second",
        )
    except OSError as exc:
        assert "second B4 raster" in str(exc)
    else:
        raise AssertionError("second raster failure was not propagated")
    native_checkpoint_runtime.cleanup_snapshot_transaction(
        work_dir,
        failed_dir,
    )
    assert not failed_dir.exists()

    # 同じ2枚を再試行し、圧縮・manifest確定・stream復元・清掃を完遂する。
    retry_id = f"20260731T000001Z-{dpi:012x}"
    retry_dir = native_checkpoint_runtime.create_snapshot_transaction(
        work_dir,
        retry_id,
    )
    snapshots = {}
    read_buffers = []
    for index, value in enumerate(values, start=1):
        source = _VirtualReadPixels(total_values, value)
        read_buffers.append(source)
        snapshots[f"raster-{index}"] = _capture(
            raster_layer_op,
            native_checkpoint_runtime,
            _VirtualImage(width, height, source),
            retry_dir,
            f"raster-{index}",
        )
    native_checkpoint_runtime.seal_snapshot_transaction(
        retry_dir,
        snapshots,
    )
    for index, value in enumerate(values, start=1):
        sink = _VirtualWritePixels(total_values, value)
        handlers._restore_raster_snapshot_stream(
            _VirtualImage(width, height, sink),
            snapshots[f"raster-{index}"],
            total_values,
        )
        assert sink.written == total_values
        assert sink.max_slice <= 262_144
    assert all(source.max_slice <= 262_144 for source in read_buffers)
    assert all(source.slice_count > 1 for source in read_buffers)
    native_checkpoint_runtime.cleanup_snapshot_transaction(
        work_dir,
        retry_dir,
    )
    assert not retry_dir.exists()
    assert not (
        work_dir / ".bmanga-save-recovery-v1" / "raster-snapshots"
    ).exists()
    return total_values * 4 * len(values)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _load_addon_modules()
    from bmanga_raster_snapshot_streaming.io import (
        native_checkpoint_runtime,
    )
    from bmanga_raster_snapshot_streaming.operators import raster_layer_op
    from bmanga_raster_snapshot_streaming.utils import handlers

    temp_root = Path(tempfile.mkdtemp(prefix="bmanga_raster_streaming_"))
    try:
        logical_bytes = 0
        with _MemoryMonitor() as memory:
            for dpi in (300, 600):
                logical_bytes += _exercise_resolution(
                    temp_root,
                    dpi,
                    raster_layer_op,
                    handlers,
                    native_checkpoint_runtime,
                )
        assert logical_bytes > 1_800 * MIB, logical_bytes
        assert memory.growth < MAX_RSS_GROWTH, memory.growth
        print(
            f"B4_LOGICAL_MIB={logical_bytes / MIB:.1f} "
            f"PEAK_RSS_GROWTH_MIB={memory.growth / MIB:.1f}",
            flush=True,
        )
        print(SENTINEL, flush=True)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
