#!/usr/bin/env python3
"""Build a 2D occupancy grid (ROS map_server format) from a MetaCam Lite recording.

Uses raw /livox/lidar scans from data_raw.mcap + the device trajectory
odom-realtime.csv, ray-casts each scan in 2D, and writes map.pgm/png + map.yaml.

  pip install mcap mcap-ros1-support numpy opencv-python-headless
  python3 mcap_to_2d_map.py <recording_dir> [-o out_dir] [--res 0.05]
         [--min-h 0.15 --max-h 1.20] [--range 15] [--info] [--jpegs]

Heights are measured above the estimated floor. Pick --max-h around the robot's
body height (D1 ~0.5 m): anything the robot would bump into must lie in
[min-h, max-h]. Raise --min-h to ignore small bumps/cables, lower it for curbs.
"""
import argparse, os, sys
import numpy as np, cv2
from mcap.reader import make_reader
from mcap_ros1.decoder import DecoderFactory


def load_odom(path):
    d = np.loadtxt(path, delimiter=",", skiprows=1, usecols=range(8))
    return d[:, 0] * 1e-9, d[:, 1:4], d[:, 4:8]  # t, xyz, quat(xyzw)


def quat_to_R(q):
    x, y, z, w = q / np.linalg.norm(q)
    return np.array([
        [1 - 2*(y*y + z*z), 2*(x*y - z*w), 2*(x*z + y*w)],
        [2*(x*y + z*w), 1 - 2*(x*x + z*z), 2*(y*z - x*w)],
        [2*(x*z - y*w), 2*(y*z + x*w), 1 - 2*(x*x + y*y)]])


def pose_at(t, T, P, Q):
    i = np.searchsorted(T, t)
    if i <= 0 or i >= len(T):
        return None  # outside trajectory
    a = (t - T[i-1]) / (T[i] - T[i-1])
    q0, q1 = Q[i-1], Q[i] if np.dot(Q[i-1], Q[i]) >= 0 else -Q[i]
    return quat_to_R((1-a)*q0 + a*q1), (1-a)*P[i-1] + a*P[i]


def cmd_info(mcap_path):
    r = make_reader(open(mcap_path, "rb")); s = r.get_summary()
    for cid, c in s.channels.items():
        sc = s.schemas.get(c.schema_id)
        print(f"{c.topic:24s} {sc.name if sc else '?':32s} {s.statistics.channel_message_counts.get(cid, 0)}")


def cmd_jpegs(mcap_path, out):
    os.makedirs(out, exist_ok=True)
    r = make_reader(open(mcap_path, "rb"), decoder_factories=[DecoderFactory()])
    n = 0
    for sch, ch, msg, d in r.iter_decoded_messages(topics=["/camera/left/jpeg", "/camera/right/jpeg"]):
        side = ch.topic.split("/")[2]
        open(f"{out}/{side}_{msg.log_time}.jpg", "wb").write(bytes(d.data)); n += 1
    print("wrote", n, "jpegs to", out)


def pcd_obstacles(rec, floor, x0, y0, res, H, W, min_h, max_h, min_pts):
    """Occupied cells from the registered cloud Preview.pcd: cells with >= min_pts points in
    [min_h, max_h] above the floor (box/table sides AND tops), closed by 1 cell, small blobs dropped.
    The raw-scan ray casting alone loses boxes seen from above (their tops are above the slab and the
    free-space votes of rays passing over them win)."""
    path = f"{rec}/Preview.pcd"
    if not os.path.exists(path):
        return np.zeros((H, W), bool)
    with open(path, "rb") as f:
        while not f.readline().startswith(b"DATA"):
            pass
        a = np.frombuffer(f.read(), dtype=np.float32).reshape(-1, 4)[:, :3]
    a = a[np.isfinite(a).all(1)]
    h = a[:, 2] - floor
    a = a[(h >= min_h) & (h < max_h)]
    ci = ((a[:, 0] - x0) / res).astype(int); ri = ((a[:, 1] - y0) / res).astype(int)
    ok = (ci >= 0) & (ci < W) & (ri >= 0) & (ri < H)
    cnt = np.zeros((H, W), np.int32); np.add.at(cnt, (ri[ok], ci[ok]), 1)
    occ = cv2.morphologyEx((cnt >= min_pts).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(occ, connectivity=8)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] < 6:
            occ[lab == i] = 0
    return occ.astype(bool)


def build(a):
    rec = a.recording
    T, P, Q = load_odom(f"{rec}/odom-realtime.csv")
    # floor height: low percentile of trajectory-near points is unreliable for handheld
    # data, so estimate from the raw scans in a first pass (world z of near-ground returns).
    r = make_reader(open(f"{rec}/data/data_raw.mcap", "rb"), decoder_factories=[DecoderFactory()])
    scans = []
    zs = []
    for sch, ch, msg, d in r.iter_decoded_messages(topics=["/livox/lidar"]):
        pose = pose_at(msg.log_time * 1e-9 if False else d.header.stamp.to_sec() if hasattr(d.header.stamp, "to_sec")
                       else d.header.stamp.secs + d.header.stamp.nsecs * 1e-9, T, P, Q)
        if pose is None:
            continue
        pts = np.array([(p.x, p.y, p.z) for p in d.points], np.float32)
        pts = pts[(np.abs(pts[:, :2]).max(1) < a.range) & (np.linalg.norm(pts, axis=1) > 0.4)]
        R, t = pose
        w = pts @ R.T.astype(np.float32) + t.astype(np.float32)
        scans.append((t, w))
        zs.append(w[::20, 2])
    zs = np.concatenate(zs)
    floor = np.percentile(zs, a.floor_pct)
    print(f"{len(scans)} scans, floor z = {floor:.2f} m (percentile {a.floor_pct})")

    allxy = np.concatenate([s[1][::10, :2] for s in scans])
    x0, y0 = np.percentile(allxy, 0.1, axis=0) - 1; x1, y1 = np.percentile(allxy, 99.9, axis=0) + 1
    res = a.res
    W, H = int((x1-x0)/res)+1, int((y1-y0)/res)+1
    occ = np.zeros(H*W, np.int32); free = np.zeros(H*W, np.int32)
    for org, w in scans:
        h = w[:, 2] - floor
        keep = (h > -0.3) & (h < a.max_h)                      # ground + body-height slab only
        w, h = w[keep], h[keep]
        hit = (h >= a.min_h)
        ci = np.clip(((w[:, 0]-x0)/res).astype(int), 0, W-1); ri = np.clip(((w[:, 1]-y0)/res).astype(int), 0, H-1)
        cells, inv = np.unique(np.stack([ri, ci, hit], 1), axis=0, return_inverse=True)  # one vote per cell per scan
        hz = np.full(len(cells), np.inf); np.minimum.at(hz, inv.ravel(), h)               # lowest return height per cell
        zs_ = org[2] - floor                                   # sensor height above floor
        o = np.array([(org[1]-y0)/res, (org[0]-x0)/res])
        fidx = []
        for (rr, cc, hh), hzi in zip(cells, hz):
            n = int(max(abs(rr-o[0]), abs(cc-o[1])))
            if n < 2:
                continue
            # 3D-aware clearing: a ray from a high sensor passes ABOVE low obstacles, so it only
            # proves a cell free where the ray itself is below the slab top (max_h). Flat 2D
            # clearing (--flat-rays) erased boxes/tables seen from above.
            k0 = 0
            if not a.flat_rays and zs_ > a.max_h + 0.05 and zs_ > hzi:
                k0 = int(np.clip(np.ceil(n * (zs_ - (a.max_h + 0.05)) / (zs_ - hzi)), 0, n))
            k = np.arange(k0, n if hh else n+1)                # stop before the obstacle cell
            if len(k) == 0:
                continue
            fidx.append(((o[0] + (rr-o[0])*k/n).astype(np.int64)*W + (o[1] + (cc-o[1])*k/n).astype(np.int64)))
        if fidx:
            free[np.unique(np.concatenate(fidx))] += 1
        oc = cells[cells[:, 2] == 1]
        occ[np.unique(oc[:, 0]*W + oc[:, 1])] += 1
    occ, free = occ.reshape(H, W), free.reshape(H, W)
    is_occ = (occ >= a.min_hits) & (occ >= 0.3*(occ+free))
    if not a.no_pcd:
        is_occ |= pcd_obstacles(rec, floor, x0, y0, res, H, W, a.min_h, a.max_h, a.pcd_min_pts)
    # drop isolated specks
    n, lab, st, _ = cv2.connectedComponentsWithStats(is_occ.astype(np.uint8), connectivity=8)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] < a.min_blob:
            is_occ[lab == i] = False
    img = np.full((H, W), 205, np.uint8)
    img[(free > 0) & ~is_occ] = 254
    img[is_occ] = 0
    img = img[::-1]                                            # map_server: row 0 = top (max y)
    os.makedirs(a.out, exist_ok=True)
    cv2.imwrite(f"{a.out}/map.pgm", img); cv2.imwrite(f"{a.out}/map.png", img)
    open(f"{a.out}/map.yaml", "w").write(
        f"image: map.pgm\nmode: trinary\nresolution: {res}\norigin: [{x0:.3f}, {y0:.3f}, 0.0]\n"
        "negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n")
    ov = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    px = np.stack([((P[:, 0]-x0)/res), (H-1-(P[:, 1]-y0)/res)], 1).astype(np.int32)
    cv2.polylines(ov, [px], False, (0, 0, 255), 2); cv2.imwrite(f"{a.out}/map_trajectory.png", ov)
    print(f"map {W}x{H} @ {res} m -> {a.out}/map.yaml")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("recording"); p.add_argument("-o", "--out")
    p.add_argument("--res", type=float, default=0.05)
    p.add_argument("--min-h", type=float, default=0.15); p.add_argument("--max-h", type=float, default=1.20)
    p.add_argument("--range", type=float, default=15.0)
    p.add_argument("--floor-pct", type=float, default=3.0)
    p.add_argument("--min-hits", type=int, default=3); p.add_argument("--min-blob", type=int, default=3)
    p.add_argument("--no-pcd", action="store_true", help="do not add obstacles from Preview.pcd")
    p.add_argument("--pcd-min-pts", type=int, default=3)
    p.add_argument("--flat-rays", action="store_true", help="old 2D free-space clearing (erases low obstacles)")
    p.add_argument("--info", action="store_true"); p.add_argument("--jpegs", action="store_true")
    a = p.parse_args(); a.out = a.out or f"{a.recording}/map2d"
    m = f"{a.recording}/data/data_raw.mcap"
    if a.info: cmd_info(m)
    elif a.jpegs: cmd_jpegs(m, f"{a.out}/jpegs")
    else: build(a)
