"""ROS-free parameter safety clamp for the follow controller."""

import dataclasses

from .follow_controller import FollowParams

# global safety caps, applied no matter what the parameters say
VX_CAP = 0.5
WZ_CAP = 0.8


def clamp_follow_params(params: FollowParams):
    """Return (params capped to VX_CAP / WZ_CAP, list of warning strings)."""
    warnings = []
    if params.max_vx > VX_CAP:
        warnings.append(f'max_vx {params.max_vx} clamped to {VX_CAP} m/s')
    if params.max_wz > WZ_CAP:
        warnings.append(f'max_wz {params.max_wz} clamped to {WZ_CAP} rad/s')
    clamped = dataclasses.replace(params, max_vx=min(params.max_vx, VX_CAP),
                                  max_wz=min(params.max_wz, WZ_CAP))
    return clamped, warnings
