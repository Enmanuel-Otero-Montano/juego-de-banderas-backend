import random
import hashlib
from datetime import date
from io import BytesIO
from PIL import Image, ImageFilter, ImageDraw

from config import settings


def pixelate_image(image_bytes: bytes, reveal_level: int, *, seed_date: date | None = None, max_level: int | None = None) -> bytes:
    """
    Devuelve la bandera procesada según reveal_level.
    - Si reveal_level >= max_level => devuelve la imagen original (sin grilla).
    - Determinístico por seed_date + reveal_level.
    """
    max_level = max_level or settings.DAILY_MAX_ATTEMPTS
    seed_date = seed_date or date.today()

    # Load image
    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # ✅ Si terminó el juego (o reveal_level alto), devolvemos la original
    if reveal_level >= max_level:
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    # Deterministic Seed
    seed_str = f"{seed_date.isoformat()}:{reveal_level}"
    seed_hash = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    rng = random.Random(seed_hash)

    # 1) Pixelation (Downscale -> Upscale)
    widths = [20, 40, 70, 110, 160, 220]  # sirve bien aunque max_attempts sea 4 (usarás levels 0..3)
    target_width = widths[min(reveal_level, len(widths) - 1)]

    w, h = img.size
    aspect = h / w
    target_h = max(1, int(target_width * aspect))

    img_small = img.resize((target_width, target_h), Image.Resampling.BILINEAR)
    img_pixelated = img_small.resize((w, h), Image.Resampling.NEAREST)

    # 2) Blur
    blur_radius = max(0.0, (5 - reveal_level) * 0.5)
    if blur_radius > 0:
        img_pixelated = img_pixelated.filter(ImageFilter.GaussianBlur(blur_radius))

    # 3) Noise (tu versión, tal cual)
    pixels = img_pixelated.load()
    noise_intensity = max(0, 30 - (reveal_level * 5))

    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            nr = rng.randint(-noise_intensity, noise_intensity)
            ng = rng.randint(-noise_intensity, noise_intensity)
            nb = rng.randint(-noise_intensity, noise_intensity)
            pixels[x, y] = (
                min(255, max(0, r + nr)),
                min(255, max(0, g + ng)),
                min(255, max(0, b + nb)),
                a
            )

    # 4) Rotation (fillcolor en RGBA)
    angle = rng.uniform(-1.5, 1.5)
    img_pixelated = img_pixelated.rotate(
        angle,
        resample=Image.Resampling.BILINEAR,
        fillcolor=(200, 200, 200, 255),
    )

    # 5) Grid overlay con alpha (ahora sí válido)
    draw = ImageDraw.Draw(img_pixelated, "RGBA")
    grid_size = rng.randint(20, 40)

    for x in range(0, w, grid_size):
        draw.line((x, 0, x, h), fill=(128, 128, 128, 50), width=1)
    for y in range(0, h, grid_size):
        draw.line((0, y, w, y), fill=(128, 128, 128, 50), width=1)

    out = BytesIO()
    img_pixelated.save(out, format="PNG")
    return out.getvalue()
