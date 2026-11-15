import sys, time, numpy as np, rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
from nav_msgs.msg import OccupancyGrid
out = sys.argv[1]
rclpy.init(); n = Node('grab_map'); got = []
n.create_subscription(OccupancyGrid, '/map', lambda m: got.append(m), QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL))
t0 = time.time()
while time.time() - t0 < 20 and not got: rclpy.spin_once(n, timeout_sec=0.2)
if not got: print('NO MAP'); sys.exit(1)
m = got[-1]; w, h = m.info.width, m.info.height
g = np.array(m.data, np.int16).reshape(h, w)
img = np.full((h, w), 205, np.uint8); img[(g >= 0) & (g < 25)] = 254; img[g >= 65] = 0
img = img[::-1]                                  # pgm row 0 = top = north
with open(out + '.pgm', 'wb') as f: f.write(b'P5\n%d %d\n255\n' % (w, h)); f.write(img.tobytes())
open(out + '.yaml', 'w').write(f"image: {out.split('/')[-1]}.pgm\nmode: trinary\nresolution: {m.info.resolution}\norigin: [{m.info.origin.position.x}, {m.info.origin.position.y}, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.25\n")
print('saved', out, w, 'x', h)
