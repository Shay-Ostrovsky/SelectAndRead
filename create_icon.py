"""Generates icon.ico for SelectAndRead."""
import os
from PIL import Image, ImageDraw

def _make_frame(size):
    img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad    = max(1, size // 10)
    radius = max(2, size // 4)
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=radius,
        fill=(37, 99, 235),          # blue-600
    )

    # Three horizontal text lines (left half)
    lw    = max(1, size // 18)
    x1    = size * 0.18
    x2    = size * 0.46
    for yf in (0.34, 0.50, 0.66):
        y = size * yf
        draw.rounded_rectangle(
            [x1, y - lw / 2, x2, y + lw / 2],
            radius=lw // 2,
            fill=(255, 255, 255),
        )

    # Sound-wave arcs (right half)
    cx  = size * 0.68
    cy  = size * 0.50
    sw  = max(1, size // 20)
    for rf in (0.11, 0.18, 0.25):
        r   = size * rf
        box = [cx - r, cy - r, cx + r, cy + r]
        draw.arc(box, start=-55, end=55, fill=(255, 255, 255), width=sw)

    # Dot at wave origin
    dr = max(1, size * 0.045)
    draw.ellipse([cx - dr, cy - dr, cx + dr, cy + dr], fill=(255, 255, 255))

    return img


def main():
    sizes  = [256, 128, 64, 48, 32, 16]
    frames = [_make_frame(s) for s in sizes]
    out    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
    frames[0].save(
        out, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[1:],
    )
    print(f"icon.ico created at {out}")


if __name__ == "__main__":
    main()
