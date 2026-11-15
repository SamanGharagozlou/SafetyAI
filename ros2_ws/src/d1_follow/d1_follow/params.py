"""ROS-free parameter safety clamp for the follow controller."""

import dataclasses

from .follow_controller import FollowParams

# global safety caps, applied no matter what the parameters say
VX_CAP = 0.5
WZ_CAP = 0.8


def clamp_follow_params(params: FollowParams):
    """Return (params capped to VX_CAP / WZ_CAP, list of warning strings)."""
    # magnitude: a negative limit must not slip past the cap as min(-1, 0.8)
    max_vx = min(abs(params.max_vx), VX_CAP)
    max_wz = min(abs(params.max_wz), WZ_CAP)
    warnings = []
    if max_vx != params.max_vx:
        warnings.append(f'max_vx {params.max_vx} clamped to {max_vx} m/s')
    if max_wz != params.max_wz:
        warnings.append(f'max_wz {params.max_wz} clamped to {max_wz} rad/s')
    return dataclasses.replace(params, max_vx=max_vx, max_wz=max_wz), warnings
