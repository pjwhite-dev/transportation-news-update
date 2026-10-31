"""Draw site_assets/live/og-card.png, the picture link previews show (LinkedIn, Slack, email). Run again after a
change to the name or the mark; it uses Pillow's own font, so it needs nothing outside the repository."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NIGHT, ICE, DIM, AMBER, LIVE = "#0A1424", "#EAF0F8", "#9FB0C8", "#F2B33D", "#2FD27A"
OUT = Path(__file__).resolve().parent.parent / "site_assets" / "live" / "og-card.png"


def main() -> None:
    scale = 2  # drawn large, then reduced, for smooth edges
    w, h = 1200 * scale, 630 * scale
    img = Image.new("RGB", (w, h), NIGHT)
    d = ImageDraw.Draw(img)
    # the radar mark, as on the page: two rings, a beam with its trail, and a lit contact
    cx, cy, r = 250 * scale, 315 * scale, 150 * scale
    for start, alpha in ((-150, 30), (-120, 46), (-102, 72)):
        layer = Image.new("RGBA", (w, h))
        ImageDraw.Draw(layer).pieslice((cx - r, cy - r, cx + r, cy + r), start, -90, fill=(242, 179, 61, alpha))
        img.paste(layer, (0, 0), layer)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=AMBER, width=10 * scale)
    r2 = int(r * 10 / 18)
    d.ellipse((cx - r2, cy - r2, cx + r2, cy + r2), outline="#7A6230", width=5 * scale)
    d.line((cx, cy, cx, cy - r), fill=AMBER, width=10 * scale)
    d.ellipse((cx - 14 * scale, cy - 14 * scale, cx + 14 * scale, cy + 14 * scale), fill=AMBER)
    bx, by = cx + int(r * 8 / 18), cy - int(r * 12 / 18)
    d.ellipse((bx - 26 * scale, by - 26 * scale, bx + 26 * scale, by + 26 * scale), fill=LIVE)

    big = ImageFont.load_default(size=76 * scale)
    mid = ImageFont.load_default(size=34 * scale)
    x = 470 * scale
    d.text((x, 200 * scale), "Advanced", font=big, fill=ICE)
    d.text((x, 290 * scale), "Transportation", font=big, fill=ICE)
    d.text((x, 380 * scale), "Live", font=big, fill=AMBER)
    d.text((x, 500 * scale), "Drones · Advanced air · AVs · Rail · Robotics", font=mid, fill=DIM)
    img.resize((1200, 630), Image.LANCZOS).save(OUT, optimize=True)
    print(OUT)


if __name__ == "__main__":
    main()
