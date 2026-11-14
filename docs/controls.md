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

## ROS 2 (`cmd_vel`)

```bash
cd ros2_ws && source /opt/ros/humble/setup.bash && source install/setup.bash
ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=172.24.1.20 robot_ip:=172.24.1.41

ros2 service call /d1_cmd_vel_bridge/stand_up std_srvs/srv/Trigger
ros2 service call /d1_cmd_vel_bridge/lie_down std_srvs/srv/Trigger
ros2 service call /d1_cmd_vel_bridge/passive  std_srvs/srv/Trigger   # emergency

ros2 run agibot_d1_bridge test_sequence   # fwd 2s, stop, rev 2s, stop, then yaw (not yet run on robot)
```

The bridge clamps speeds (default 1.0 m/s, 0.5 m/s, 1.5 rad/s), zeroes values below the
SDK minimum, and stops if `cmd_vel` is silent for 0.5 s.
