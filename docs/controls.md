# AgiBot D1 (wheel-foot, zsl-1w) – Basic Controls

Verified on the real robot: **stand up** and **lie down** both work via the SDK.

## Setup (this robot)

The SDK runs over a **direct Ethernet cable between the robot and the onboard Jetson**.
The Jetson is the SDK host. Wi-Fi is still used for SSH, the camera stream and everything else.

| Item | Value |
|---|---|
| Robot, Wi-Fi `Event_MyLAG` | `172.24.1.41` (SSH: `firefly` / `firefly`) |
| Robot, Ethernet `eth0` (cable to Jetson) | `192.168.168.168/24` |
| Jetson, Wi-Fi | `172.24.0.239` (SSH: `sepa`) |
| Jetson, Ethernet `enP8p1s0` (cable to robot) | `192.168.168.10/24`, SDK UDP port `43988` |
| Robot model | `zsl-1w` (`ROBOT_TYPE=XGW`) |
| SDK | submodule `third_party/agibot_D1_Edu-Ultra` (Jetson: `lib/zsl-1w/aarch64`) |

The cable subnet has no gateway on either side, so both machines keep their Wi-Fi default route.
Don't give the Jetson `192.168.168.100` or `.120`: robot boot scripts (`start_app_node.sh`,
`start_gpsd_chrony.sh`, `start_lcm.sh` in `/opt/app_launch`) wait for those addresses and
act when they appear.

### Robot-side config (done 2026-11-15)

| File | Setting | Previous value (in `*.bak`) |
|---|---|---|
| `/etc/netplan/01-netcfg.yaml` | `eth0`: `dhcp4: false`, `ignore-carrier: true`, `addresses: [192.168.168.168/24]` | `eth0`: `dhcp4: true` |
| `/opt/app_launch/start_motion_control_xgw.sh` | `export SDK_CLIENT_IP="192.168.168.168"` | `"172.24.1.41"` |
| `/opt/export/config/sdk_config.yaml` | `target_ip: "192.168.168.10"` | `"172.24.1.20"` |

The `*.bak_pre_ros` files are the factory originals from before any of this.

The Jetson's cable address comes from a NetworkManager profile, `robot-link`:
```bash
sudo nmcli con add type ethernet con-name robot-link ifname enP8p1s0 \
  ipv4.method manual ipv4.addresses 192.168.168.10/24 ipv4.never-default yes \
  ipv6.method disabled connection.autoconnect yes connection.autoconnect-priority 10
sudo nmcli con up robot-link
```

To apply netplan changes on the robot, reload only `eth0`; `netplan apply` would also touch Wi-Fi:
`sudo netplan generate && sudo networkctl reload && sudo networkctl reconfigure eth0`.

### Link at boot

All the settings above are saved on disk, so they survive a reboot. On the robot, `/` is an
overlay saved under `/userdata/rootfs_overlay`. The catch is timing: the cable only gets carrier
once both sides are up (about 2.5 min after robot boot in testing). `mc_ctrl` starts earlier and
fails to bind `SDK_CLIENT_IP` if `eth0` has no address yet. Two layers handle this:

- **Netplan `ignore-carrier: true`:** networkd assigns `192.168.168.168` without waiting for carrier.
- **Boot services in `scripts/network/`:**

| Machine | Service | What it does |
|---|---|---|
| Robot | `robot-link.service` → `robot-link-up.sh` | Runs **before `robot-launch`**. Brings `eth0` up and makes sure it has `192.168.168.168/24`, with or without carrier. |
| Jetson | `jetson-link.service` → `jetson-link-up.sh` | Retries `nmcli con up robot-link` for up to 10 min until `enP8p1s0` has `192.168.168.10`. Doesn't hold up the Jetson's boot. |

Both only touch the cable interface (not Wi-Fi), are safe to re-run, and never reboot.
To install, copy `scripts/network/` to the machine and run:
```bash
sudo ./install.sh robot     # on the robot (installed 2026-11-15)
sudo ./install.sh jetson    # on the Jetson
```
To check: `systemctl status robot-link` / `journalctl -b -u jetson-link`.
To remove: `sudo systemctl disable --now <name>-link` and delete the unit and script.

### Applying SDK IP changes: restart only the motion program

`SDK_CLIENT_IP` and `target_ip` are read only when `mc_ctrl` starts.

> **Do not reboot the robot**, and don't restart the whole `robot-launch` service.
> Restarting `mc_ctrl` cuts motor control, so **lie the robot down first**.

Kill `mc_ctrl` and its start script. `robot-launch` starts them again within about 1 s:
```bash
MC=$(pgrep -x mc_ctrl); SH=$(ps -o ppid= -p $MC | tr -d ' ')
ps -o pid,cmd -p $MC,$SH            # check: ./mc_ctrl r  and  bash .../start_motion_control_xgw.sh
sudo kill $MC $SH
sleep 2; sudo cat /proc/$(pgrep -x mc_ctrl)/environ | tr '\0' '\n' | grep SDK_CLIENT_IP
sudo ss -ulpn | grep mc_ctrl        # should listen on 192.168.168.168:43997 and :43900
```
Don't find these processes with `pgrep -f <script name>` over SSH: the pattern also matches the
SSH shell running the command.

> Earlier, on Wi-Fi, `mc_ctrl` sometimes failed to bind the SDK port after a boot
> (`connect time out`, nothing on UDP 43997). If that happens, use the restart above.
> The old suggestion `ROBOT_NET_INTERFACES=wlan0` would now be wrong, because the SDK is on `eth0`.

### Switching the SDK back to a PC on Wi-Fi

Set `SDK_CLIENT_IP="172.24.1.41"` and `target_ip: "<PC IP>"` (the `*.bak` values), then
restart `mc_ctrl` as above. Only one SDK host gets data at a time.

## SDK commands (Python)

Run on the Jetson (Python 3.10):
```python
import sys; sys.path.insert(0, "third_party/agibot_D1_Edu-Ultra/lib/zsl-1w/aarch64")   # x86_64 on a PC
import mc_sdk_zsl_1w_py as m, time

a = m.HighLevel()
a.initRobot("192.168.168.10", 43988, "192.168.168.168")   # local IP, local port, robot IP (cable)
time.sleep(3); assert a.checkConnect()

a.standUp()                 # stand      -> returns 0 on success
a.move(0.3, 0.0, 0.0)       # vx, vy, yaw_rate (only while standing)
a.lieDown()                 # lie down (joints locked) – don't call while moving
a.passive()                 # emergency: damping, robot drops
```

- `move` limits: vx ±0.05–3.7 m/s, vy ±0.1–1 m/s, yaw ±0.1–3 rad/s. Below the minimum, send exactly `0`.
- Return `0` = OK. Common errors: `0x3007` state change failed, `0x3010` motor disabled, `0x3011` motor fault.
- Control mode (`getCurrentCtrlmode()`): `0` damping/idle, `1` standing, `51` lying down (as observed).
- Keep the process alive: no SDK data for **3 s** → the robot goes into damping and lies down.
- While an SDK program runs, the remote controller is overridden.

## ROS 2 bridge (`agibot_d1_bridge`)

```bash
cd ros2_ws && source /opt/ros/humble/setup.bash && source install/setup.bash
ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=192.168.168.10 robot_ip:=192.168.168.168   # on the Jetson
```

**Commands in**

| Topic / service | Type | What it does |
|---|---|---|
| `cmd_vel` | `Twist` | Drive with the active gait: `x`=vx, `y`=vy, `angular.z`=yaw rate |
| `gait` | `String` | `move` (default) \| `crawl` \| `climb`; switching stops motion first and cancels the old gait |
| `attitude_cmd` | `Twist` | Body wobble/height while standing: `angular.x/y/z` = roll/pitch/yaw rate, `linear.z` = height rate (each ±0.5) |
| `robot_cmd` | `String` | `stand_up` \| `lie_down` \| `damp` \| `shake_hand` \| `cancel_crawl` \| `cancel_climb` |
| `~/stand_up` `~/lie_down` `~/damp` `~/passive` `~/shake_hand` | `Trigger` | Same posture commands as services (`passive` = `damp`) |

```bash
ros2 service call /d1_cmd_vel_bridge/stand_up std_srvs/srv/Trigger
ros2 service call /d1_cmd_vel_bridge/damp     std_srvs/srv/Trigger   # emergency, robot drops
ros2 topic pub --once /robot_cmd std_msgs/msg/String "{data: shake_hand}"
ros2 topic pub --once /gait std_msgs/msg/String "{data: crawl}"
ros2 run agibot_d1_bridge test_sequence   # fwd 2s, stop, rev 2s, stop, then yaw (not yet run on robot)
```

**Data out** (published only while the SDK is connected, `state_rate` = 10 Hz)

| Topic | Type | Source |
|---|---|---|
| `imu` | `sensor_msgs/Imu` | quaternion, gyro, accel |
| `odom` | `nav_msgs/Odometry` | SDK position + orientation, body velocity |
| `battery` | `sensor_msgs/BatteryState` | percentage only (other fields NaN) |
| `joint_states` | `sensor_msgs/JointState` | `FR/FL/RR/RL` x `abad/hip/knee/foot` position, velocity, torque |
| `ctrl_mode` | `std_msgs/Int32` | SDK control mode |
| `connected` | `std_msgs/Bool` | SDK link status (always published) |

**Safety built in:** speeds are clamped to the SDK limit for the active gait *and* your caps
(`max_vx/vy/wz`, default 1.0 / 0.5 / 1.5); values below the SDK minimum become 0;
`cmd_vel` and `attitude_cmd` stop after 0.5 s of silence.

| Gait | vx | vy | yaw (min..max) |
|---|---|---|---|
| move | 0.05..3.7 m/s | 0.1..1.0 | 0.1..3.0 rad/s |
| crawl | 0.1..3.0 | 0.1..0.5 | 0.1..1.0 |
| climb | 0.1..1.0 | 0.1..1.0 | 0.1..3.0 |

Not exposed: `zsl-1`-only acrobatics (`jump`, `frontJump`, `backflip`, `twoLegStand`) - not
available on the wheel-foot robot.

## Keyboard control

With the bridge running, in another terminal (ROS sourced):
```bash
ros2 run agibot_d1_bridge keyboard_teleop            # --lin 0.2 --ang 0.4 --hold 0.5
```

| Key | Action | Key | Action |
|---|---|---|---|
| `w` / `s` (or Up/Down) | forward / back | `1` | stand up |
| `a` / `d` (or Left/Right) | turn left / right | `2` | lie down |
| `q` / `e` | strafe left / right | `3` | shake hand |
| space | stop | `c` | cycle gait (move/crawl/climb) |
| `+` / `-` | speed up / down | `x` | **damp (emergency, robot drops)** |

Gestures (from `d1w_gestures`, robot must be standing, i.e. `ctrl_mode` = 1; press `1` first).
Any key cancels a running gesture. `--gesture-scale 1.0` (default) shrinks the gestures if set lower,
`--no-reset` skips the `stand_up` re-centre after each gesture.
Inside a sequence such as `g` (greet), only the last step re-centres: `stand_up` while already
standing made the robot lie down and rise between the bow and the shake hand (*unverified* on the robot).

| Key | Gesture | Key | Gesture |
|---|---|---|---|
| `4` | nod | `8` | squat |
| `5` | shake (no) | `9` | wiggle |
| `6` | tilt | `0` | look around |
| `7` | bow | `r` | sway circle |
| `g` | greet (bow + shake hand) | | |

Gestures stream body rates on `attitude_cmd` (the bridge forwards them to `attitudeControl`), so
they need no second SDK connection. Pitch sign and the `stand_up` re-centre are unverified on hardware.

Terminals don't report key releases, so the robot keeps moving for `--hold` seconds after the
last key event: hold a key to move, release to stop. Ctrl-C stops and quits (it does not damp;
the bridge watchdog does that ~3 s later if the SDK link goes quiet).
