"""Colour palette for all chart output.

Pure data module — no imports, no side effects.
Import this freely without pulling in matplotlib.
"""

PALETTE = {
    "teal": "#2C7A7B",
    "mint": "#68D391",
    "sky": "#63B3ED",
    "purple": "#B83280",
    "peach": "#F6AD55",
}

_RGB = {
    "teal": (44 / 255, 122 / 255, 123 / 255),
    "mint": (104 / 255, 211 / 255, 145 / 255),
    "sky": (99 / 255, 179 / 255, 237 / 255),
    "purple": (184 / 255, 50 / 255, 128 / 255),
    "peach": (246 / 255, 173 / 255, 85 / 255),
}

GRADIENTS = {
    "sky_purple": ["sky", "purple"],
    "peach_purple": ["peach", "purple"],
    "mint_sky_teal": ["mint", "sky", "teal"],
}

GRADIENT_NAMES = tuple(GRADIENTS.keys())


def build_colormap(gradient: str, reverse: bool = False):
    if gradient not in GRADIENTS:
        raise ValueError(
            f"Unknown gradient '{gradient}'. "
            f"Valid options: {', '.join(GRADIENT_NAMES)}"
        )

    from matplotlib.colors import LinearSegmentedColormap

    rgb_stops = [_RGB[name] for name in GRADIENTS[gradient]]
    if reverse:
        rgb_stops = list(reversed(rgb_stops))
    return LinearSegmentedColormap.from_list(gradient, rgb_stops, N=256)
