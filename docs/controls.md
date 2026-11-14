# AgiBot D1 (wheel-foot, zsl-1w) – Basic Controls

Verified on the real robot: **stand up** and **lie down** both work via the SDK.

## Setup (this robot)

| Item | Value |
|---|---|
| Robot IP (Wi-Fi `Event_MyLAG`) | `172.24.1.41` (SSH: `firefly` / `firefly`) |
| This PC | `172.24.1.20`, UDP port `43988` |
| Robot model | `zsl-1w` (`ROBOT_TYPE=XGW`) |
| SDK | submodule `third_party/agibot_D1_Edu-Ultra` |

Robot-side config (already done, backups are `*.bak_pre_ros`):
- `/opt/export/config/sdk_config.yaml`: `target_ip: "172.24.1.20"`
- `/opt/app_launch/start_motion_control_xgw.sh`: `export SDK_CLIENT_IP="172.24.1.41"`

> **After every robot reboot** the motion program may fail to bind the SDK port
> (`connect time out`, nothing listening on UDP 43997). Temporary fix:
> `sudo systemctl restart robot-launch` on the robot. Permanent fix (not applied):
> add `Environment="ROBOT_NET_INTERFACES=wlan0"` to
> `/etc/systemd/system/robot-launch.service` and reboot.

## SDK commands (Python)

```python
import sys; sys.path.insert(0, "third_party/agibot_D1_Edu-Ultra/lib/zsl-1w/x86_64")
import mc_sdk_zsl_1w_py as m, time

a = m.HighLevel()
a.initRobot("172.24.1.20", 43988, "172.24.1.41")   # local IP, local port, robot IP
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
ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=172.24.1.20 robot_ip:=172.24.1.41
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

Terminals don't report key releases, so the robot keeps moving for `--hold` seconds after the
last key event: hold a key to move, release to stop. Ctrl-C stops and quits (it does not damp;
the bridge watchdog does that ~3 s later if the SDK link goes quiet).
