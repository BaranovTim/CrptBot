"""Render the Vanth torch to every Android icon slot. 8x supersampled.

    python3 brand/make_icons.py

Needs Pillow. Regenerates every launcher, adaptive-foreground and
notification PNG in place, so editing the geometry here and re-running
is the only supported way to change the icon -- the PNGs are output,
not source. The vector master is brand/vanth.svg and the two must be
kept in step by hand; they are small enough that this is cheaper than
adding an SVG renderer to the toolchain.
"""
from PIL import Image, ImageDraw
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "mobile", "android", "app", "src", "main", "res")
SS = 8                      # supersample factor
BG = (17, 19, 23, 255)      # Obsidian surface #111317
GREEN = (0, 255, 171, 255)
GREY = (139, 144, 160, 255)
CLEAR = (0, 0, 0, 0)


def bez(p0, p1, p2, p3, n=64):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u*u*u*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t*t*t*p3[0],
                    u*u*u*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t*t*t*p3[1]))
    return out


def flame_outer():
    # M32 12 C41 23 41 30 32 37 C23 30 23 23 32 12 Z
    return bez((32,12),(41,23),(41,30),(32,37)) + bez((32,37),(23,30),(23,23),(32,12))


def flame_inner():
    # M32 20 C36 25 36 29 32 32 C28 29 28 25 32 20 Z
    return bez((32,20),(36,25),(36,29),(32,32)) + bez((32,32),(28,29),(28,25),(32,20))


def draw_mark(d, s, ox, oy, flame, grey, hole):
    """Artwork is authored in a 64-unit box; s scales it, ox/oy offset it."""
    def P(pts): return [(ox + x*s, oy + y*s) for x, y in pts]
    d.polygon(P(flame_outer()), fill=flame)
    if hole is not None:
        d.polygon(P(flame_inner()), fill=hole)
    # crossguard and stem, round caps drawn as capsules
    for (x1,y1,x2,y2,w) in ((23,39,41,39,3.5), (32,41,32,53,4.0)):
        r = w*s/2
        d.line(P([(x1,y1),(x2,y2)]), fill=grey, width=int(round(w*s)))
        for (cx,cy) in P([(x1,y1),(x2,y2)]):
            d.ellipse([cx-r, cy-r, cx+r, cy+r], fill=grey)


# The mark's real bounding box inside the 64-unit box, round caps included:
# x 21.25..42.75, y 12..55. Its centre is y=33.5, NOT 32 -- so centring the
# BOX leaves the mark 1.5 units low, and the stem tip breaks out of the
# bottom of the 66dp circle that a launcher mask guarantees. Centre the MARK.
MARK_CY = (12 + 55) / 2
MARK_HALF_H = (55 - 12) / 2


def render(size, *, background, inset, flame, grey, rounded):
    big = size * SS
    img = Image.new("RGBA", (big, big), CLEAR)
    d = ImageDraw.Draw(img)
    if background:
        d.rounded_rectangle([0, 0, big-1, big-1], radius=int(big*14/64), fill=BG)
    art = big * inset
    s = art / 64.0
    ox = (big - art) / 2
    # shift so the mark's centre, not the box's, lands on the canvas centre
    oy = big / 2 - MARK_CY * s
    draw_mark(d, s, ox, oy, flame, grey, BG if background else CLEAR)
    return img.resize((size, size), Image.LANCZOS)


def save(img, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)


DENS = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}
made = []

for name, mult in DENS.items():
    # legacy launcher: full bleed, dark rounded square
    px = int(48 * mult)
    save(render(px, background=True, inset=1.0, flame=GREEN, grey=GREY,
                rounded=True), f"{RES}/mipmap-{name}/ic_launcher.png")
    made.append(f"mipmap-{name}/ic_launcher.png {px}px")

    # adaptive foreground: 108dp canvas, art inside the 72dp safe zone
    px = int(108 * mult)
    # 0.83, NOT 72/108.
    #
    # The 72dp figure applies to ARTWORK, and this artwork is authored in a
    # 64-unit box it does not fill: the torch spans 43 of those 64 units, so
    # insetting the whole box to 72/108 insets it twice and the mark lands at
    # 45% of the icon. 0.83 puts the torch at 0.83 * 43/64 = 56% of the
    # canvas, just inside the 66dp circle (61%) that is guaranteed visible
    # under every launcher mask.
    save(render(px, background=False, inset=0.83, flame=GREEN, grey=GREY,
                rounded=False), f"{RES}/mipmap-{name}/ic_launcher_foreground.png")
    made.append(f"mipmap-{name}/ic_launcher_foreground.png {px}px")

    # notification: white silhouette, Android tints it
    px = int(24 * mult)
    white = (255, 255, 255, 255)
    save(render(px, background=False, inset=1.0, flame=white, grey=white,
                rounded=False), f"{RES}/drawable-{name}/ic_notification.png")
    made.append(f"drawable-{name}/ic_notification.png {px}px")

print("\n".join(made))
print("total files:", len(made))
