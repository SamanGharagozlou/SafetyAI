import sys, os, math, sqlite3, numpy as np, cv2, yaml
sys.path.insert(0, '/usr/lib/python3/dist-packages')
RUN = open(os.path.join(os.environ.get('EVAL_DIR', os.path.expanduser('~/d1_maps/eval')), 'current_run.txt')).read().strip()
S = os.environ.get('EVAL_DIR', os.path.expanduser('~/d1_maps/eval'))
names = ['A_raw', 'B_biasfix', 'C_gravity', 'D_refine']
paths = {'A_raw': np.load(S + '/path_raw.npy'), 'B_biasfix': np.load(S + '/path_cor.npy')}
paths['C_gravity'] = paths['D_refine'] = paths['B_biasfix']
tiles = []
print(f"{'variant':12s} {'nodes':>6s} {'LC ok':>6s} {'LC rej':>7s} | {'occ m2':>7s} {'free m2':>8s} {'stray%':>7s} {'erode-survive%':>15s}")
for n in names:
    log = open(f'{S}/var_{n}/rtab.log', errors='replace').read()
    import re
    log = re.sub(r'\x1b\[[0-9;]*m', '', log)
    up = re.findall(r'rtabmap \((\d+)\).*WM=(\d+)', log)
    nodes = int(up[-1][1]) if up else 0
    lc_ok = len(re.findall(r'Loop closure \d+ -> \d+ (?:accepted|added)|Added loop closure|loop closure detected', log, re.I))
    lc_rej = len(re.findall(r'Rejected loop closure|Loop closure \d+ -> \d+ rejected', log))
    base = f'{RUN}_var_{n}_map'
    if not os.path.exists(base + '.pgm'):
        print(f'{n:12s} {nodes:6d} {lc_ok:6d} {lc_rej:7d} | (no map saved)')
        continue
    y = yaml.safe_load(open(base + '.yaml')); img = cv2.imread(base + '.pgm', cv2.IMREAD_UNCHANGED); H, W = img.shape; res = y['resolution']; ox, oy = y['origin'][:2]
    occ = (img == 0); free = (img == 254)
    nb = cv2.filter2D(occ.astype(np.uint8), -1, np.ones((3, 3), np.float32), borderType=cv2.BORDER_CONSTANT) - occ.astype(np.uint8)
    stray = (occ & (nb < 2)).sum() / max(1, occ.sum())
    er = cv2.erode(occ.astype(np.uint8), np.ones((3, 3), np.uint8))
    surv = er.sum() / max(1, occ.sum())
    print(f'{n:12s} {nodes:6d} {lc_ok:6d} {lc_rej:7d} | {occ.sum()*res*res:7.1f} {free.sum()*res*res:8.1f} {100*stray:7.1f} {100*surv:15.1f}')
    k = 3; rgb = np.zeros((H, W, 3), np.uint8); rgb[img == 205] = (60, 60, 60); rgb[free] = (63, 47, 36); rgb[occ] = (60, 90, 255)
    big = cv2.resize(rgb, (W * k, H * k), interpolation=cv2.INTER_NEAREST)
    p = paths[n]; mv = np.nonzero(np.hypot(np.gradient(p[:, 1]), np.gradient(p[:, 2])) > 1e-4)[0]
    pts = np.array([[int(((a - ox) / res + .5) * k), int(((H - 1) - (b - oy) / res + .5) * k)] for a, b in p[:, 1:3]], np.int32)
    cv2.polylines(big, [pts], False, (255, 200, 60), 2); cv2.circle(big, tuple(pts[0]), 7, (0, 220, 0), -1)
    cv2.putText(big, n, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    tiles.append(big)
if tiles:
    h = max(t.shape[0] for t in tiles)
    cv2.imwrite(S + '/variants_compare.png', np.hstack([np.pad(t, ((0, h - t.shape[0]), (0, 4), (0, 0)), constant_values=30) for t in tiles]))
    print('saved variants_compare.png')
