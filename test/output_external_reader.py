"""B-MANGA出力器に依存しない画像・PSD・PDFの最小reader。"""

from __future__ import annotations

from dataclasses import dataclass
import re
import struct
from pathlib import Path


def _u16(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from(">H", data, offset)[0], offset + 2


def _i16(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from(">h", data, offset)[0], offset + 2


def _u32(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from(">I", data, offset)[0], offset + 4


def _i32(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from(">i", data, offset)[0], offset + 4


def read_flat_image(path: Path, image_module) -> dict[str, object]:
    """Pillowをreaderとして形式・寸法・DPI・代表色を取得する。"""

    with image_module.open(path) as opened:
        opened.load()
        dpi = opened.info.get("dpi")
        rgb = opened.convert("RGB")
        extrema = rgb.getextrema()
        return {
            "format": str(opened.format or ""),
            "mode": str(opened.mode),
            "size": [int(opened.width), int(opened.height)],
            "dpi": [float(dpi[0]), float(dpi[1])] if dpi else None,
            "extrema": [[int(a), int(b)] for a, b in extrema],
        }


@dataclass
class _PsdChannel:
    channel_id: int
    length: int


@dataclass
class _PsdLayer:
    name: str
    top: int
    left: int
    bottom: int
    right: int
    channels: list[_PsdChannel]
    alpha: bytes | None = None

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)


def _pascal_name(data: bytes, offset: int) -> tuple[str, int]:
    start = offset
    length = data[offset]
    offset += 1
    raw = data[offset:offset + length]
    offset += length
    offset = start + ((offset - start + 3) // 4) * 4
    return raw.decode("macroman", errors="replace"), offset


def _layer_name(data: bytes, offset: int, end: int) -> str:
    mask_length, offset = _u32(data, offset)
    offset += mask_length
    blend_length, offset = _u32(data, offset)
    offset += blend_length
    name, offset = _pascal_name(data, offset)
    while offset + 12 <= end:
        signature = data[offset:offset + 4]
        key = data[offset + 4:offset + 8]
        length = struct.unpack_from(">I", data, offset + 8)[0]
        payload = offset + 12
        payload_end = payload + length
        if signature not in {b"8BIM", b"8B64"} or payload_end > end:
            break
        if key == b"luni" and length >= 4:
            units = struct.unpack_from(">I", data, payload)[0]
            raw = data[payload + 4:payload + 4 + units * 2]
            name = raw.decode("utf-16be", errors="replace")
        offset = payload_end + (length % 2)
    return name


def _unpack_bits(payload: bytes, expected: int) -> bytes:
    out = bytearray()
    offset = 0
    while offset < len(payload) and len(out) < expected:
        control = payload[offset]
        offset += 1
        if control <= 127:
            count = control + 1
            out.extend(payload[offset:offset + count])
            offset += count
        elif control >= 129:
            count = 257 - control
            if offset >= len(payload):
                break
            out.extend(payload[offset:offset + 1] * count)
            offset += 1
    if len(out) != expected:
        raise AssertionError(f"PSD PackBits長が不正です: {len(out)} != {expected}")
    return bytes(out)


def _decode_channel(data: bytes, width: int, height: int) -> bytes:
    compression = struct.unpack_from(">H", data, 0)[0]
    expected = width * height
    if compression == 0:
        raw = data[2:2 + expected]
        if len(raw) != expected:
            raise AssertionError("PSD raw channelが途中で切れています")
        return raw
    if compression != 1:
        raise AssertionError(f"未対応のPSD圧縮です: {compression}")
    table_end = 2 + height * 2
    lengths = struct.unpack_from(f">{height}H", data, 2) if height else ()
    offset = table_end
    rows = []
    for length in lengths:
        rows.append(_unpack_bits(data[offset:offset + length], width))
        offset += length
    raw = b"".join(rows)
    if len(raw) != expected:
        raise AssertionError("PSD RLE channelの画素数が不正です")
    return raw


def _resolution_from_resources(resources: bytes) -> float | None:
    offset = 0
    resolution = None
    while offset + 10 <= len(resources):
        if resources[offset:offset + 4] != b"8BIM":
            break
        resource_id = struct.unpack_from(">H", resources, offset + 4)[0]
        name_length = resources[offset + 6]
        name_total = 1 + name_length
        name_padded = name_total + (name_total % 2)
        size_offset = offset + 6 + name_padded
        if size_offset + 4 > len(resources):
            break
        size = struct.unpack_from(">I", resources, size_offset)[0]
        payload = size_offset + 4
        payload_end = payload + size
        if payload_end > len(resources):
            break
        if resource_id == 1005 and size >= 16:
            fixed = struct.unpack_from(">I", resources, payload)[0]
            resolution = float(fixed) / 65536.0
        offset = payload_end + (size % 2)
    return resolution


def read_psd(path: Path) -> dict[str, object]:
    """PSD仕様のheader/resource/layer record/channelを直接読む。"""

    data = path.read_bytes()
    if len(data) < 30 or data[:4] != b"8BPS":
        raise AssertionError("PSD signatureが不正です")
    version = struct.unpack_from(">H", data, 4)[0]
    channels = struct.unpack_from(">H", data, 12)[0]
    height = struct.unpack_from(">I", data, 14)[0]
    width = struct.unpack_from(">I", data, 18)[0]
    depth = struct.unpack_from(">H", data, 22)[0]
    color_mode = struct.unpack_from(">H", data, 24)[0]
    offset = 26
    color_length, offset = _u32(data, offset)
    offset += color_length
    resource_length, offset = _u32(data, offset)
    resources = data[offset:offset + resource_length]
    offset += resource_length
    layer_mask_length, offset = _u32(data, offset)
    layer_mask_end = offset + layer_mask_length
    layers: list[_PsdLayer] = []
    if layer_mask_length:
        layer_info_length, offset = _u32(data, offset)
        layer_info_end = offset + layer_info_length
        count, offset = _i16(data, offset)
        for _ in range(abs(count)):
            top, offset = _i32(data, offset)
            left, offset = _i32(data, offset)
            bottom, offset = _i32(data, offset)
            right, offset = _i32(data, offset)
            channel_count, offset = _u16(data, offset)
            layer_channels = []
            for _channel in range(channel_count):
                channel_id, offset = _i16(data, offset)
                channel_length, offset = _u32(data, offset)
                layer_channels.append(_PsdChannel(channel_id, channel_length))
            if data[offset:offset + 4] != b"8BIM":
                raise AssertionError("PSD layer signatureが不正です")
            offset += 12
            extra_length, offset = _u32(data, offset)
            extra_end = offset + extra_length
            name = _layer_name(data, offset, extra_end)
            layers.append(_PsdLayer(name, top, left, bottom, right, layer_channels))
            offset = extra_end
        for layer in layers:
            for channel in layer.channels:
                channel_data = data[offset:offset + channel.length]
                if len(channel_data) != channel.length:
                    raise AssertionError("PSD layer channelが途中で切れています")
                if channel.channel_id == -1:
                    layer.alpha = _decode_channel(channel_data, layer.width, layer.height)
                offset += channel.length
        if offset > layer_info_end + 1 or layer_info_end > layer_mask_end:
            raise AssertionError("PSD layer sectionの長さが不正です")
    rows = []
    for layer in layers:
        alpha = layer.alpha or b""
        rows.append({
            "name": layer.name,
            "bounds": [layer.left, layer.top, layer.right, layer.bottom],
            "alpha_min": min(alpha) if alpha else None,
            "alpha_max": max(alpha) if alpha else None,
            "alpha_zero": alpha.count(0) if alpha else 0,
            "alpha_nonzero": sum(1 for value in alpha if value) if alpha else 0,
        })
    return {
        "version": version,
        "channels": channels,
        "size": [width, height],
        "depth": depth,
        "color_mode": color_mode,
        "dpi": _resolution_from_resources(resources),
        "layers": rows,
    }


_NUMBER = rb"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_MEDIA_BOX = re.compile(
    rb"/MediaBox\s*\[\s*(" + _NUMBER + rb")\s+(" + _NUMBER
    + rb")\s+(" + _NUMBER + rb")\s+(" + _NUMBER + rb")\s*\]"
)


def read_pdf(path: Path) -> dict[str, object]:
    """Pillow出力PDFを独立に構文走査し、ページ数とMediaBoxを読む。"""

    data = path.read_bytes()
    if not data.startswith(b"%PDF-"):
        raise AssertionError("PDF signatureが不正です")
    page_count = len(re.findall(rb"/Type\s*/Page\b", data))
    boxes = []
    for match in _MEDIA_BOX.finditer(data):
        x0, y0, x1, y1 = (float(value) for value in match.groups())
        boxes.append([x1 - x0, y1 - y0])
    color_spaces = sorted({item.decode("ascii") for item in re.findall(rb"/ColorSpace\s*/(Device\w+)", data)})
    return {
        "version": data[5:8].decode("ascii", errors="replace"),
        "page_count": page_count,
        "media_boxes": boxes,
        "color_spaces": color_spaces,
    }
