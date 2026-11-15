"""Places for the guest app (layout only: guests never see the floor plan). No ROS, pure Python."""
import yaml


def load_places(path):
    """places.yaml -> {name: {x, y, yaw, label, aliases, description}} (the same file the gateway loads with places_file:=)."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    out = {}
    for name, p in raw.items():
        if not isinstance(p, dict) or 'x' not in p or 'y' not in p:
            raise ValueError(f'place {name!r} needs x and y')
        out[str(name)] = {'x': float(p['x']), 'y': float(p['y']), 'yaw': float(p.get('yaw', 0.0)), 'label': p.get('label') or str(name),
                          'aliases': list(p.get('aliases') or []), 'description': p.get('description') or ''}
    return out
