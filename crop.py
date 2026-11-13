"""Server-side zoom: crop the full-resolution camera frame before asking the user to move.

Uses macOS `sips` (no third-party packages). The browser uploads a full-resolution copy of
each frame for local cropping. The crop is sent when a request needs finer detail;
if reading stalls, full-task diagnosis may also receive the high-resolution full view."""
from pathlib import Path
import struct
import subprocess
import tempfile


def jpeg_size(data):
    """(width, height) from the first SOF marker, or None."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = struct.unpack('>H', data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack('>HH', data[i + 5:i + 9])
            return w, h
        i += 2 + length
    return None


def expand(roi, pad=0.25, min_side=0.2):
    """Grow a 0–1 box by `pad` of its size on each side, at least `min_side` wide/high, kept in frame."""
    w = max(min_side, roi['width'] * (1 + 2 * pad))
    h = max(min_side, roi['height'] * (1 + 2 * pad))
    w, h = min(1.0, w), min(1.0, h)
    cx, cy = roi['x'] + roi['width'] / 2, roi['y'] + roi['height'] / 2
    x = min(max(0.0, cx - w / 2), 1 - w)
    y = min(max(0.0, cy - h / 2), 1 - h)
    return {'x': round(x, 4), 'y': round(y, 4), 'width': round(w, 4), 'height': round(h, 4)}


def crop(data, roi, max_side=1280):
    """Crop a 0–1 box from JPEG bytes; returns {'image', 'roi'} or None when it cannot crop."""
    size = jpeg_size(data)
    if not size:
        return None
    W, H = size
    x, y = int(roi['x'] * W), int(roi['y'] * H)
    w, h = max(16, int(roi['width'] * W)), max(16, int(roi['height'] * H))
    w, h = min(w, W - x), min(h, H - y)
    if w < 16 or h < 16:
        return None
    with tempfile.TemporaryDirectory(prefix='agentxr-crop-') as tmp:
        src, out = Path(tmp) / 'in.jpg', Path(tmp) / 'out.jpg'
        src.write_bytes(data)
        # Two calls: within one call sips resizes before cropping.
        try:
            subprocess.run(['sips', '--cropToHeightWidth', str(h), str(w), '--cropOffset', str(y), str(x), str(src), '--out', str(out)],
                           check=True, capture_output=True, timeout=10)
            if max(w, h) > max_side:
                subprocess.run(['sips', '-Z', str(max_side), str(out)], check=True, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        if not out.exists():
            return None
        return {'image': out.read_bytes(), 'roi': {'x': x / W, 'y': y / H, 'width': w / W, 'height': h / H}}
