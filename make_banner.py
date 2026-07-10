# -*- coding: utf-8 -*-
"""Генерация баннера THEBENUA SHOP (Зевс вместо ангела)."""

import math
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 640
img = Image.new("RGB", (W, H), "#dfe9fb")
d = ImageDraw.Draw(img)

# сетка на фоне
for x in range(0, W, 48):
    d.line([(x, 0), (x, H)], fill="#cdddf6", width=1)
for y in range(0, H, 48):
    d.line([(y * 0, y), (W, y)], fill="#cdddf6", width=1)

# белая диагональная полоса
d.polygon([(0, H), (W * 0.42, H), (W * 0.62, 0), (W * 0.2, 0)], fill="#f4f8ff")


def font(size, bold=True):
    for name in ("arialbd.ttf" if bold else "arial.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def emoji_font(size):
    for name in ("seguiemj.ttf", "NotoColorEmoji.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return None


# заголовок
title = "THEBENUA SHOP"
f = font(88)
tw = d.textlength(title, font=f)
x, y = 60, H // 2 - 40
d.text((x + 4, y + 4), title, font=f, fill="#9db8e8")
d.text((x, y), title, font=f, fill="#5b8ef0")

# Зевс: молния и лавровый венок (греческая мифология вместо ангела)
ef = emoji_font(220)
if ef is not None:
    try:
        d.text((W - 330, 40), "\u26a1", font=ef, embedded_color=True)
    except TypeError:
        d.text((W - 330, 40), "\u26a1", font=ef, fill="#f2c94c")
cx, cy, r = W - 220, 150, 160
for i in range(48):
    a = math.pi * (0.15 + 0.7 * i / 47)
    for sign in (1, -1):
        px = cx + sign * r * math.sin(a)
        py = cy - r * math.cos(a) + 90
        leaf_a = a * sign
        d.ellipse([px - 14, py - 7, px + 14, py + 7], fill="#7aa15c")

sub = "ZEUS  •  Цифровые товары 24/7"
f2 = font(40, bold=False)
d.text((62, y + 110), sub, font=f2, fill="#7d94bd")

os.makedirs("assets", exist_ok=True)
img.save(os.path.join("assets", "banner.png"))
print("saved assets/banner.png")
