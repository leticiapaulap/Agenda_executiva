"""Gera o ícone original da Agenda com desenho geométrico e antialiasing."""
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    destination = Path(__file__).resolve().parents[1] / "assets"
    destination.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((32, 32, 992, 992), radius=220, fill="#14243b")
    draw.rounded_rectangle((215, 235, 809, 827), radius=68, fill="#ffffff")
    draw.rounded_rectangle((215, 235, 809, 423), radius=68, fill="#3a78dc")
    draw.rectangle((215, 342, 809, 423), fill="#3a78dc")
    for x in (358, 666):
        draw.rounded_rectangle((x - 28, 166, x + 28, 305), radius=27, fill="#a9c8f5")
    for x, y in ((337, 525), (493, 525), (649, 525), (337, 669), (493, 669)):
        draw.rounded_rectangle((x - 35, y - 35, x + 35, y + 35), radius=15, fill="#d9e6f8")
    draw.rounded_rectangle((599, 619, 699, 719), radius=24, fill="#235ec9")
    draw.line(((622, 668), (644, 690), (680, 646)), fill="#ffffff", width=12, joint="curve")
    image = image.resize((256, 256), Image.Resampling.LANCZOS)
    image.save(destination / "app.png")
    image.save(destination / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()
