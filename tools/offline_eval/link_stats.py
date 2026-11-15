"""Compare RTAB-Map neighbor links between two databases (same data, different settings) matched by node timestamps.
B = odometry-only links, D = links refined by visual registration (RGBD/NeighborLinkRefining)."""
import math, sqlite3, sys
import numpy as np

def load(db):
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True); c = con.cursor()
    st = {i: s for i, s in c.execute('select id,stamp from Node')}
    out = []
    for f, t, tr in c.execute('select from_id,to_id,transform from Link where type=0'):
        m = np.frombuffer(tr, np.float32)
        if len(m) != 12: continue
        out.append((st[f], st[t], float(m[3]), float(m[7]), math.atan2(m[4], m[0])))
    return out

A, B = load(sys.argv[1]), load(sys.argv[2])
print(f'links: {sys.argv[1].split("/")[-1]}: {len(A)}, {sys.argv[2].split("/")[-1]}: {len(B)}')
bi = {(round(a[0], 1), round(a[1], 1)): a for a in B}
pairs = []
for a in A:
    k = (round(a[0], 1), round(a[1], 1))
    if k in bi:
        pairs.append((a, bi[k]))
print(f'links matched by timestamps: {len(pairs)}')
ta = np.array([math.hypot(p[0][2], p[0][3]) for p in pairs]); tb = np.array([math.hypot(p[1][2], p[1][3]) for p in pairs])
ya = np.array([p[0][4] for p in pairs]); yb = np.array([p[1][4] for p in pairs])
print(f'total path length: first {ta.sum():.2f} m, second {tb.sum():.2f} m')
mv = ta > 0.05
if mv.sum() >= 8:
    r = tb[mv] / ta[mv]
    print(f'translation ratio second/first on {int(mv.sum())} moving links: median {np.median(r):.2f}  (p25 {np.percentile(r, 25):.2f}, p75 {np.percentile(r, 75):.2f})')
d = np.degrees(((yb - ya) + math.pi) % (2 * math.pi) - math.pi)
print(f'rotation difference per link: median {np.median(d):+.2f} deg, rms {np.sqrt(np.mean(d**2)):.2f} deg; links differing by >5 deg: {int((np.abs(d) > 5).sum())}')
print(f'total rotation: first {np.degrees(np.abs(ya)).sum():.0f} deg, second {np.degrees(np.abs(yb)).sum():.0f} deg')
