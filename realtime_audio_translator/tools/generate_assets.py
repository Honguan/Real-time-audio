from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def generate_icon(root: Path | None = None) -> None:
    root = root or Path.cwd()
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    size = 512
    image = Image.new("RGBA", (size, size), "#101418")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((36, 92, 476, 420), radius=60, fill="#1f6feb")
    draw.rounded_rectangle((72, 136, 440, 376), radius=38, fill="#f6f8fa")
    draw.rectangle((116, 240, 396, 278), fill="#101418")
    draw.ellipse((104, 216, 152, 302), fill="#1f6feb")
    draw.ellipse((360, 216, 408, 302), fill="#1f6feb")
    font = ImageFont.load_default()
    draw.text((205, 186), "RT", fill="#101418", font=font)
    image.save(assets / "icon.png")
    image.save(assets / "icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])


if __name__ == "__main__":
    generate_icon()
