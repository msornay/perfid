# Vendored from https://github.com/ericl/diplomacy-mapper (AGPL-3.0)
# Ported to Python 3 / Pillow — stripped to hold-only rendering (no arrows)

from PIL import Image, ImageDraw

from map_renderer.data import (
    COLOR_AUSTRIA, COLOR_ENGLAND, COLOR_FRANCE, COLOR_GERMANY,
    COLOR_ITALY, COLOR_NEUTRAL, COLOR_RUSSIA, COLOR_TURKEY,
    DEFAULT_AUSTRIA, DEFAULT_ENGLAND, DEFAULT_FRANCE, DEFAULT_GERMANY,
    DEFAULT_ITALY, DEFAULT_RUSSIA, DEFAULT_TURKEY, DIP, IMAGE_ARMY,
    IMAGE_FLEET, IMAGE_MAP, IMAGE_NAMES, INDEX_COLOR, INDEX_COORD,
    UNALIGNED, is_land,
)


class Context:
    nation = None


ENGLAND = ('ENG', COLOR_ENGLAND)
RUSSIA = ('RUS', COLOR_RUSSIA)
FRANCE = ('FRA', COLOR_FRANCE)
ITALY = ('ITA', COLOR_ITALY)
TURKEY = ('TUR', COLOR_TURKEY)
GERMANY = ('GER', COLOR_GERMANY)
AUSTRIA = ('AUS', COLOR_AUSTRIA)
N_COLOR = 1
N_NAME = 0

init = {}
armies = []
fleets = []
land = {}
occupied = set()


def reset():
    """Clear all mutable state for a fresh render."""
    global init, armies, fleets, land, occupied
    init = {}
    armies = []
    fleets = []
    land = {}
    occupied = set()
    # Re-apply default territory colors
    for t in UNALIGNED:
        set_color(t, COLOR_NEUTRAL)
    for defaults, nation in [
        (DEFAULT_ENGLAND, ENGLAND), (DEFAULT_GERMANY, GERMANY),
        (DEFAULT_FRANCE, FRANCE), (DEFAULT_ITALY, ITALY),
        (DEFAULT_AUSTRIA, AUSTRIA), (DEFAULT_TURKEY, TURKEY),
        (DEFAULT_RUSSIA, RUSSIA),
    ]:
        context(nation)
        for t in defaults:
            _set(t)


def context(n):
    Context.nation = n


def set_color(t, color):
    x = DIP[t]
    init[x[INDEX_COLOR]] = color


def get(t):
    return land[t][0] if t in land else None


def _set(t):
    x = DIP[t]
    land[t] = Context.nation
    set_color(t, Context.nation[N_COLOR])


def army_hold(t):
    armies.append((t, Context.nation))
    occupied.add(t[:3])


def fleet_hold(t):
    fleets.append((t, Context.nation))
    occupied.add(t[:3])


def write_substitution_image(out):
    import os
    data_dir = os.path.dirname(IMAGE_MAP)

    img = Image.open(IMAGE_MAP).convert('RGBA')
    img_army = Image.open(IMAGE_ARMY).convert('RGBA')
    img_fleet = Image.open(IMAGE_FLEET).convert('RGBA')
    mask = Image.open(os.path.join(data_dir, 'mask.png')).convert('RGBA')
    outline = Image.open(os.path.join(data_dir, 'outline.png')).convert('RGBA')

    def withoutalpha(c):
        return (c[0], c[1], c[2])

    buf = []
    for color in img.getdata():
        noalpha = withoutalpha(color)
        if noalpha in init:
            buf.append(init[noalpha])
        else:
            buf.append(color)
    img.putdata(buf)

    for army in armies:
        coord = DIP[army[0]][INDEX_COORD]
        land_owner = get(army[0])
        army_owner = army[1][0]
        if land_owner != army_owner:
            img.paste(outline, (coord[0], coord[1] - 15), outline)
            img.paste(army[1][1], (coord[0] + 1, coord[1] - 14), mask)
        img.paste(img_army, coord, img_army)

    for fleet in fleets:
        coord = DIP[fleet[0]][INDEX_COORD]
        land_owner = get(fleet[0])
        fleet_owner = fleet[1][0]
        if land_owner != fleet_owner:
            img.paste(outline, (coord[0], coord[1] - 15), outline)
            img.paste(fleet[1][1], (coord[0] + 1, coord[1] - 14), mask)
        img.paste(img_fleet, coord, img_fleet)

    text = Image.open(IMAGE_NAMES).convert('RGBA')
    img.paste(text, (0, 0), text)
    img.save(out, 'PNG')


# Initialize default territory colors
for t in UNALIGNED:
    set_color(t, COLOR_NEUTRAL)
for defaults, nation in [
    (DEFAULT_ENGLAND, ENGLAND), (DEFAULT_GERMANY, GERMANY),
    (DEFAULT_FRANCE, FRANCE), (DEFAULT_ITALY, ITALY),
    (DEFAULT_AUSTRIA, AUSTRIA), (DEFAULT_TURKEY, TURKEY),
    (DEFAULT_RUSSIA, RUSSIA),
]:
    context(nation)
    for t in defaults:
        _set(t)
