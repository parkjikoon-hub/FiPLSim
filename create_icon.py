"""FiPLSim 아이콘 생성 스크립트 — 데스크탑(ICO) + 웹/모바일(PNG) + 파비콘"""
from PIL import Image, ImageDraw, ImageFont
import os
import base64

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _draw_icon(size=512):
    """고해상도 아이콘 이미지 생성"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size / 256  # scale factor

    # Background — rounded-square gradient (모바일 친화)
    margin = int(8 * s)
    radius = int(48 * s)
    # 배경 사각형
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=radius,
        fill=(180, 30, 30, 255),
    )
    # 안쪽 그라데이션 효과
    for i in range(int(110 * s), 0, -1):
        r = int(220 - i / s * 0.8)
        g = int(50 - i / s * 0.15)
        b = int(40 - i / s * 0.15)
        r, g, b = max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
        cx, cy = size // 2, size // 2
        draw.ellipse([cx - i, cy - i, cx + i, cy + i], fill=(r, g, b, 255))

    # ── 배관 구조 (흰색) ──
    pipe_color = (255, 255, 255, 240)
    pw = int(18 * s)

    # 교차배관 (수평)
    draw.rectangle(
        [int(40 * s), int(115 * s), int(216 * s), int(115 * s) + pw],
        fill=pipe_color,
    )
    # 라이저 (수직)
    draw.rectangle(
        [int(118 * s), int(45 * s), int(118 * s) + pw, int(200 * s)],
        fill=pipe_color,
    )

    # ── 가지배관 (밝은 파란색) ──
    bw = int(8 * s)
    branch_color = (180, 210, 255, 230)
    for x_pos in [60, 90, 160, 190]:
        draw.rectangle(
            [int(x_pos * s), int(78 * s), int(x_pos * s) + bw, int(115 * s)],
            fill=branch_color,
        )

    # ── 스프링클러 헤드 (파란 원) ──
    head_r = int(6 * s)
    head_color = (80, 170, 255, 255)
    for x_pos in [64, 94, 164, 194]:
        cx = int(x_pos * s)
        cy = int(72 * s)
        draw.ellipse([cx - head_r, cy - head_r, cx + head_r, cy + head_r], fill=head_color)

    # ── 물방울 ──
    drop_color = (60, 150, 255, 210)
    dr = int(4 * s)
    for x_pos in [64, 94, 164, 194]:
        cx = int(x_pos * s)
        top = int(58 * s)
        draw.polygon(
            [(cx, top - dr * 2), (cx - dr, top), (cx + dr, top)],
            fill=drop_color,
        )

    # ── 불꽃 아이콘 (하단) ──
    fy = int(168 * s)
    # 바깥 불꽃 (주황)
    draw.polygon(
        [
            (int(128 * s), fy - int(22 * s)),
            (int(113 * s), fy + int(12 * s)),
            (int(120 * s), fy + int(6 * s)),
            (int(116 * s), fy + int(22 * s)),
            (int(128 * s), fy + int(12 * s)),
            (int(140 * s), fy + int(22 * s)),
            (int(136 * s), fy + int(6 * s)),
            (int(143 * s), fy + int(12 * s)),
        ],
        fill=(255, 150, 20, 240),
    )
    # 안쪽 불꽃 (노랑)
    draw.polygon(
        [
            (int(128 * s), fy - int(10 * s)),
            (int(121 * s), fy + int(6 * s)),
            (int(124 * s), fy + int(3 * s)),
            (int(122 * s), fy + int(14 * s)),
            (int(128 * s), fy + int(6 * s)),
            (int(134 * s), fy + int(14 * s)),
            (int(132 * s), fy + int(3 * s)),
            (int(135 * s), fy + int(6 * s)),
        ],
        fill=(255, 220, 70, 240),
    )

    # ── 텍스트 "FiPLSim" ──
    font_size = int(24 * s)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("arialbd.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    text = "FiPLSim"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    tx = (size - tw) // 2
    ty = int(210 * s)
    draw.text((tx + int(2 * s), ty + int(2 * s)), text, fill=(0, 0, 0, 120), font=font)
    draw.text((tx, ty), text, fill=(255, 255, 255, 255), font=font)

    return img


def create_all_icons():
    """ICO(데스크탑) + PNG(웹/모바일) + 파비콘 생성"""
    # 고해상도 원본
    img_512 = _draw_icon(512)

    # ── 1. Windows ICO ──
    ico_path = os.path.join(BASE_DIR, "fiplsim.ico")
    img_256 = img_512.resize((256, 256), Image.LANCZOS)
    img_256.save(ico_path, format="ICO",
                 sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"  ICO: {ico_path}")

    # ── 2. 웹/모바일 PNG (192x192, 512x512) ──
    for sz in [192, 512]:
        png_path = os.path.join(BASE_DIR, f"fiplsim_{sz}.png")
        img_512.resize((sz, sz), Image.LANCZOS).save(png_path, format="PNG")
        print(f"  PNG {sz}x{sz}: {png_path}")

    # ── 3. 파비콘 (32x32 PNG → Streamlit page_icon용) ──
    favicon_path = os.path.join(BASE_DIR, "favicon.png")
    img_512.resize((32, 32), Image.LANCZOS).save(favicon_path, format="PNG")
    print(f"  Favicon: {favicon_path}")

    # ── 4. apple-touch-icon (180x180) ──
    apple_path = os.path.join(BASE_DIR, "apple-touch-icon.png")
    img_512.resize((180, 180), Image.LANCZOS).save(apple_path, format="PNG")
    print(f"  Apple Touch: {apple_path}")

    return ico_path


if __name__ == "__main__":
    print("FiPLSim 아이콘 생성 중...")
    create_all_icons()
    print("완료!")
