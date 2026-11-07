from PIL import Image, ImageDraw, ImageFont
import os

src = r"C:\Users\user\.cursor\projects\d-65\assets\c__Users_user_AppData_Roaming_Cursor_User_workspaceStorage_f78237b5b9efebad1de8d6527cd05367_images_image-52ed10d0-9966-4fbf-b3d7-211352c1684b.png"
out = r"d:\65\assets\screens\pepe_limit_annotated.png"

im = Image.open(src).convert("RGBA")
w, h = im.size
overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
d = ImageDraw.Draw(overlay)


def font(size):
    for p in [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ]:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


f_big = font(34)
f_med = font(26)
f_sm = font(22)


def box(xy, text, fill, tfill=(255, 255, 255, 255), fnt=None):
    fnt = fnt or f_med
    x, y = xy
    pad = 12
    bbox = d.multiline_textbbox((0, 0), text, font=fnt, spacing=6)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.rounded_rectangle([x, y, x + tw + pad * 2, y + th + pad * 2], radius=10, fill=fill)
    d.multiline_text((x + pad, y + pad), text, font=fnt, fill=tfill, spacing=6)
    return tw + pad * 2, th + pad * 2


d.rounded_rectangle([16, 16, w - 16, 95], radius=12, fill=(11, 14, 17, 235))
d.text((36, 38), "НА ЭТОМ ГРАФИКЕ СЕЙЧАС: НЕ ВХОДИ", font=f_big, fill=(240, 185, 11, 255))

# 1 green triangles
cx, cy = int(w * 0.30), int(h * 0.70)
d.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], fill=(14, 203, 129, 255), outline=(0, 0, 0, 255), width=3)
bw, bh = box(
    (24, int(h * 0.48)),
    "1) Зелёный треугольник\n= ПРИМЕР в прошлом\n«цена отбилась вверх»\nэто НЕ кнопка купить",
    (14, 203, 129, 235),
    (0, 0, 0, 255),
)
d.line([(24 + bw // 2, int(h * 0.48) + bh), (cx, cy)], fill=(14, 203, 129, 255), width=5)

# 2 yellow POC
px, py = int(w * 0.76), int(h * 0.46)
d.ellipse([px - 14, py - 14, px + 14, py + 14], fill=(240, 185, 11, 255), outline=(0, 0, 0, 255), width=3)
bw2, bh2 = box(
    (int(w * 0.48), int(h * 0.18)),
    "2) Жёлтая линия\n= объёмный уровень\nпросто ориентир",
    (240, 185, 11, 235),
    (0, 0, 0, 255),
)
d.line([(int(w * 0.48) + bw2 // 2, int(h * 0.18) + bh2), (px, py)], fill=(240, 185, 11, 255), width=5)

# 3 WAIT
wx, wy = int(w * 0.84), int(h * 0.60)
d.ellipse([wx - 14, wy - 14, wx + 14, wy + 14], fill=(246, 70, 93, 255), outline=(0, 0, 0, 255), width=3)
bw3, bh3 = box(
    (int(w * 0.52), int(h * 0.68)),
    "3) Надпись ЖДИ\n= руки в карманах\nвход только когда будет\n«ЛОНГ/ШОРТ СЕЙЧАС»",
    (246, 70, 93, 235),
    (255, 255, 255, 255),
)
d.line([(int(w * 0.52) + bw3 // 2, int(h * 0.68) + 20), (wx, wy)], fill=(246, 70, 93, 255), width=5)

d.rounded_rectangle([16, h - 78, w - 16, h - 16], radius=10, fill=(11, 14, 17, 240))
d.text(
    (32, h - 58),
    "Правило: треугольники — учиться глазами. Ордер — только при «СЕЙЧАС».",
    font=f_sm,
    fill=(255, 255, 255, 255),
)

Image.alpha_composite(im, overlay).convert("RGB").save(out, quality=93)
print(out)
