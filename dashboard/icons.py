"""SVG icon bodies and image helpers (rendered as base64 data-URI imgs)."""

import base64

from dash import html

from dashboard.theme import ACCENT


def _svg_b64(svg_str: str) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(svg_str.encode()).decode()


def _icon_img(svg_body, w=18, h=18, color="currentColor"):
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f'{svg_body}</svg>'
    )
    return html.Img(src=_svg_b64(svg), width=w, height=h,
                    style={"display":"block","flexShrink":"0","opacity":"0.85"})

# Pre-built icons used in sidebar
_ICO_GRID    = '<rect x="3" y="3" width="7" height="7" rx="1.6"/><rect x="14" y="3" width="7" height="7" rx="1.6"/><rect x="3" y="14" width="7" height="7" rx="1.6"/><rect x="14" y="14" width="7" height="7" rx="1.6"/>'
_ICO_LIST    = '<line x1="9" y1="6" x2="20" y2="6"/><line x1="9" y1="12" x2="20" y2="12"/><line x1="9" y1="18" x2="20" y2="18"/><circle cx="4.5" cy="6" r="1.4"/><circle cx="4.5" cy="12" r="1.4"/><circle cx="4.5" cy="18" r="1.4"/>'
_ICO_TREND   = '<polyline points="3 17 9 11 13 14 21 6"/><polyline points="16 6 21 6 21 11"/>'
_ICO_CLOCK   = '<path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/><polyline points="3 4 3 8.5 7.5 8.5"/><line x1="12" y1="9" x2="12" y2="12.4"/><line x1="12" y1="12.4" x2="14.7" y2="13.8"/>'
_ICO_DOC     = '<rect x="5" y="3" width="14" height="18" rx="2.2"/><line x1="8.5" y1="8" x2="15.5" y2="8"/><line x1="8.5" y1="12" x2="15.5" y2="12"/><line x1="8.5" y1="16" x2="12.5" y2="16"/>'
_ICO_DOLLAR  = '<circle cx="12" cy="12" r="8.5"/><path d="M14.5 9c-.5-1-1.5-1.5-2.6-1.5-1.4 0-2.5.8-2.5 2 0 2.7 5.2 1.4 5.2 4.1 0 1.2-1.2 2-2.6 2-1.1 0-2.1-.5-2.6-1.5"/><line x1="12" y1="6" x2="12" y2="18"/>'
_ICO_SEARCH  = '<circle cx="11" cy="11" r="6.2"/><line x1="20" y1="20" x2="15.6" y2="15.6"/>'
_ICO_SLIDERS = '<line x1="4" y1="8" x2="20" y2="8"/><circle cx="9" cy="8" r="2.3"/><line x1="4" y1="16" x2="20" y2="16"/><circle cx="15" cy="16" r="2.3"/>'
_ICO_PLUS_SM = '<line x1="7.5" y1="2.5" x2="7.5" y2="12.5" stroke-width="2.4"/><line x1="2.5" y1="7.5" x2="12.5" y2="7.5" stroke-width="2.4"/>'
_ICO_CHEVRON_LEFT  = '<polyline points="14 7 9 12 14 17"/>'
_ICO_CHEVRON_RIGHT = '<polyline points="10 7 15 12 10 17"/>'
_ICO_TARGET = '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5.5"/><circle cx="12" cy="12" r="2"/>'
_ICO_GLOBE  = '<circle cx="12" cy="12" r="8.5"/><ellipse cx="12" cy="12" rx="4.2" ry="8.5"/><line x1="3.5" y1="12" x2="20.5" y2="12"/>'

def _icon(body, active=False):
    color = ACCENT if active else "#9aa0ad"
    return _icon_img(body, color=color)

def _icon_sm(body, color="#9aa0ad", size=15):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
           f'viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.9" stroke-linecap="round">{body}</svg>')
    return html.Img(src=_svg_b64(svg), width=size, height=size,
                    style={"display":"block","flexShrink":"0"})

def _icon_src(svg_body, color, w=18, h=18):
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f'{svg_body}</svg>'
    )
    return _svg_b64(svg)
