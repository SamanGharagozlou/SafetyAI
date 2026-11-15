"""Places and map image for the guest app. No ROS, no numpy/cv2 (pure Python), so it runs anywhere the gateway runs."""
import os
import re
import struct
import zlib

import yaml


def load_places(path):
    """places.yaml -> {name: {x, y, yaw, label, aliases, description}} (the same file the gateway loads with places_file:=)."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    out = {}
    for name, p in raw.items():
        if not isinstance(p, dict) or 'x' not in p or 'y' not in p:
            raise ValueError(f'place {name!r} needs x and y')
        out[str(name)] = {'x': float(p['x']), 'y': float(p['y']), 'yaw': float(p.get('yaw', 0.0)), 'label': p.get('label') or str(name),
                          'aliases': list(p.get('aliases') or []), 'description': p.get('description') or ''}
    return out


def _read_pgm(path):
    with open(path, 'rb') as f:
        data = f.read()
    m = re.match(rb'P5\s+(?:#[^\n]*\n\s*)*(\d+)\s+(\d+)\s+(\d+)\s', data)
    if not m:
        raise ValueError('not a binary PGM (P5)')
    w, h, mx = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if mx > 255:
        raise ValueError('16-bit PGM not supported')
    return w, h, data[m.end():m.end() + w * h]


def _png_gray(w, h, rows):
    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    raw = b''.join(b'\x00' + r for r in rows)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b'')


def load_map(yaml_path, margin_m=1.5):
    """Render a Nav2 map.yaml as a cropped grayscale PNG (free = white, obstacle = black, unknown = grey 205).

    Returns {'png': bytes, 'w','h' (px), 'res' (m/px), 'x0' (world x of the left edge), 'y0' (world y of the bottom edge)}.
    World -> image pixel:  px = (x - x0) / res,  py = h - (y - y0) / res.
    """
    with open(yaml_path) as f:
        meta = yaml.safe_load(f)
    img = meta['image'] if os.path.isabs(meta['image']) else os.path.join(os.path.dirname(os.path.abspath(yaml_path)), meta['image'])
    w, h, px = _read_pgm(img)
    res, (ox, oy) = float(meta['resolution']), [float(v) for v in meta['origin'][:2]]
    occ_t = float(meta.get('occupied_thresh', 0.65))
    negate = int(meta.get('negate', 0))
    lut = bytearray(256)
    for v in range(256):
        p = (v / 255.0) if negate else (255 - v) / 255.0
        lut[v] = 0 if p > occ_t else (255 if v >= 250 else 205)           # trinary: anything that is not clearly free/occupied is unknown
    pix = px.translate(bytes(lut))
    known = [i for i, v in enumerate(pix) if v != 205]
    if not known:
        raise ValueError('map has no known cells')
    rows_k, cols_k = [i // w for i in known], [i % w for i in known]
    m = int(round(margin_m / res))
    r0, r1 = max(0, min(rows_k) - m), min(h - 1, max(rows_k) + m)
    c0, c1 = max(0, min(cols_k) - m), min(w - 1, max(cols_k) + m)
    rows = [pix[r * w + c0:r * w + c1 + 1] for r in range(r0, r1 + 1)]
    cw, ch = c1 - c0 + 1, r1 - r0 + 1
    return {'png': _png_gray(cw, ch, rows), 'w': cw, 'h': ch, 'res': res, 'x0': ox + c0 * res, 'y0': oy + (h - 1 - r1) * res}
