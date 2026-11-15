# Making the D1 bridge survive a power cycle

## The problem

Three processes sit between a voice call and the robot:

| Process | Port | systemd | Survives reboot |
|---|---|---|---|
| `d1-vapi-server` (node, public edge) | `0.0.0.0:8090` | yes | yes |
| `d1-ros-agent` (rclpy, owns the robot) | `127.0.0.1:8091` | yes | yes |
| **ROS bridge** (`agibot_d1_bridge`) | — | **no** | **no** |

The bridge is hand-started in a terminal. The Jetson is powered **from the robot
battery**, so it loses power whenever the battery runs down — and the bridge does
not come back. The two units do.

That failure is quiet, which is what makes it expensive. With the bridge down the
public endpoint still answers `200`, and before the agent's `link_ok` fix it served
the last plausible-looking posture. A voice agent then tells a guest the robot is
"stationary" while it is in fact lying on the floor, unreachable.

## What this installs

- **`d1-bridge.service`** — the bridge as a unit, `Restart=always`, enabled at boot.
- **`ordering.conf`** — a drop-in making `d1-ros-agent` start *after* the bridge.
- **`d1-bridge-watchdog.{sh,service,timer}`** — self-heal every 20 s.

### Why the watchdog restarts the *bridge* and never the *agent*

There is a DDS matching-order bug: if the agent starts first, its subscriptions
match (`count_publishers` returns 1) but **callbacks never fire**. QoS is identical
on both ends, so it is not QoS. Measured: restarting the **agent** does *not* fix
it. Restarting the **bridge** while the agent is live fixes it instantly.

Ordering alone is not enough, because systemd calls the bridge "started" the moment
the process execs — long before it publishes. The watchdog closes that gap.

### Why it waits, and why it has a cooldown

While this was being built, `/status` sampled once a second read:

```
T F F F F T F F F T
```

on a robot that was standing and completely healthy. That looked like a flaky link.
It wasn't. `/connected` was publishing at **19.98 Hz in a repeating
`false,false,true` pattern** — 218 false to 76 true over ten seconds — because
`cmd_vel_bridge` polled the SDK's `checkConnect()` faster than the robot's heartbeat
updated. Every sampler was aliasing against a 2:1 square wave.

That is now **fixed at the source**: `connected` is latched over a 10 s window
(raw value still exposed as `connected_raw`), and callbacks run on their own
executor thread. Fourteen consecutive samples now read true.

The watchdog still requires **3 consecutive** failures and holds a **5-minute
cooldown**, as defence in depth. Deployed against the *old* signal it would have
struck out within seconds and restarted the bridge every five minutes forever — and
a restart loop is strictly worse than a hand-started process, because a bridge that
is always restarting is never up long enough to re-pair. A restart only cures the
matching-order *wedge*, which is persistent. If that signal ever regresses, the
cooldown is what prevents a loop.

It reads `link_ok` from the agent's own `:8091/status` (falling back to `connected`
on older agents). A TCP check would pass happily while the bridge served nulls.

## Installing

Three values in `d1-bridge.service` are **assumptions that must be confirmed on the
box first** — the workspace path, the ROS distro, and the launch command. A unit
with any of these wrong restart-loops, which is worse than a hand-started process
because nobody is watching it.

`install.sh` refuses to proceed if `agibot_d1_bridge` is not resolvable.

```bash
scp -r deploy/jetson sepa@d1-jetson.tail5ec076.ts.net:~/
ssh sepa@d1-jetson.tail5ec076.ts.net 'sudo bash ~/jetson/install.sh'
```

Then actually reboot, because surviving a reboot is the entire point:

```bash
sudo reboot
systemctl is-active d1-bridge d1-ros-agent d1-vapi-server
curl -s http://127.0.0.1:8091/status
```

## Not fixed here

- **`networkmanager-cleanup.service` is enabled** and wipes saved wifi on every
  boot; `wlan0` then has no IP when `mc_ctrl` starts, so it fails to bind the SDK
  port. This recurs on every reboot and is a separate fix.
- **The battery.** Nothing here helps if the robot runs flat — that is what takes
  the Jetson down in the first place.
- **`ctrl_mode` naming.** The robot emits `0/1/18/21/51`; the SDK documents only
  `0/1/3`, and `d1_dashboard`'s `MODE_NAMES` has no entry for `21` and calls `18`
  "moving". Anything that speaks a mode name aloud needs a map covering what the
  robot actually emits, with an explicit "unknown mode N" fallback.
