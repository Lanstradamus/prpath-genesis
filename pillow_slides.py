"""
Pillow Slide Renderer — Text-heavy slides for listicle/educational content.

Complements the HTML templates (which handle app-screen mockups).
Pillow handles:
- Numbered listicle slides (D1 format)
- Do This / Not That comparison slides (D2)
- Cheat Code reveal slides (D3)
- "Things Strong People Track" enumeration slides (D4)
- Cover hooks with stock photo backgrounds
- Simple text-on-dark slides

Output: 1080x1350 PNGs matching PRPath design language.

Usage:
    from pillow_slides import (
        numbered_slide, text_slide, comparison_slide, cover_hook_slide
    )

    numbered_slide(num=1, title="You don't remember last week's numbers",
                   output="slide_02.png")
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pathlib import Path
import textwrap
import random
import os

# ─── PRPath Design System ──────────────────────────────────────

# Colors matching shared/styles.css (real PRPath tokens)
BG_DARK = (28, 28, 30)      # #1C1C1E — real app background
BG_CARD = (58, 58, 60)      # #3A3A3C
ORANGE = (255, 107, 53)     # #FF6B35 — PRPath brand
ORANGE_DEEP = (255, 59, 0)  # #FF3B00
WHITE = (255, 255, 255)
GRAY = (142, 142, 147)      # #8E8E93
GRAY_LIGHT = (200, 200, 205)

# Tier colors (from StrengthStandards.swift — NO "Freak")
TIER_COLORS = {
    "Beginner":     (255, 59, 48),    # #FF3B30
    "Novice":       (255, 149, 0),    # #FF9500
    "Intermediate": (255, 204, 0),    # #FFCC00
    "Advanced":     (52, 199, 89),    # #34C759
    "Elite":        (0, 176, 255),    # #00B0FF
    "World Class":  (175, 82, 222),   # #AF52DE
}

# Dimensions
WIDTH = 1080
HEIGHT = 1350

# Font paths (Windows)
FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"

# Directories
ASSETS_DIR = Path(__file__).parent / "assets"
BRAND_DIR = Path(__file__).parent / "templates" / "html" / "shared"


# ─── Helpers ──────────────────────────────────────────────────

def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REGULAR
    return ImageFont.truetype(path, size)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Wrap text to fit within max_width pixels."""
    words = text.split()
    lines = []
    current = []
    for word in words:
        test = " ".join(current + [word])
        bbox = font.getbbox(test)
        if bbox[2] - bbox[0] <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_text_centered(draw, y: int, text: str, font, color, max_width: int = 980):
    """Draw text centered horizontally at given y. Returns final y after text."""
    lines = _wrap_text(text, font, max_width)
    for line in lines:
        bbox = font.getbbox(line)
        w = bbox[2] - bbox[0]
        draw.text(((WIDTH - w) / 2, y), line, font=font, fill=color)
        y += font.size + 10
    return y


def _draw_prpath_watermark(img: Image.Image, draw: ImageDraw.ImageDraw):
    """Add small PRPath watermark in bottom right."""
    font = _load_font(28, bold=True)
    text = "PRPath"
    bbox = font.getbbox(text)
    w = bbox[2] - bbox[0]
    # Add slight opacity by drawing with darker color
    draw.text((WIDTH - w - 50, HEIGHT - 60), text, font=font, fill=ORANGE)


def _apply_dark_overlay(img: Image.Image, opacity: float = 0.6) -> Image.Image:
    """Add dark overlay to an image for text legibility."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, int(255 * opacity)))
    result = Image.alpha_composite(img.convert("RGBA"), overlay)
    return result.convert("RGB")


# ─── Slide Types ──────────────────────────────────────────────

def cover_hook_slide(
    hook_text: str,
    accent_word: str = None,
    bg_image_path: str = None,
    output_path: str = None,
) -> str:
    """
    Hook slide for listicle carousels. Big bold text on dark background
    (or stock photo with overlay).

    Example: "5 reasons you're stuck at the same weight"
    """
    if bg_image_path and Path(bg_image_path).exists():
        img = Image.open(bg_image_path).convert("RGB")
        img = img.resize((WIDTH, HEIGHT))
        img = _apply_dark_overlay(img, opacity=0.7)
    else:
        img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)

    draw = ImageDraw.Draw(img)

    # Main hook text — centered, large
    title_font = _load_font(96, bold=True)

    if accent_word and accent_word.upper() in hook_text.upper():
        # Split into parts to highlight accent word
        lines = _wrap_text(hook_text, title_font, 920)
        total_h = len(lines) * (96 + 20)
        y = (HEIGHT - total_h) // 2

        for line in lines:
            words = line.split()
            # Measure each word to center the line
            spaces = [" "] * (len(words) - 1)
            tokens = []
            for i, w in enumerate(words):
                tokens.append(w)
                if i < len(words) - 1:
                    tokens.append(" ")
            line_w = sum(title_font.getbbox(t)[2] - title_font.getbbox(t)[0] for t in tokens)
            x = (WIDTH - line_w) // 2
            for t in tokens:
                color = ORANGE if t.upper() == accent_word.upper() else WHITE
                draw.text((x, y), t, font=title_font, fill=color)
                x += title_font.getbbox(t)[2] - title_font.getbbox(t)[0]
            y += 96 + 20
    else:
        _draw_text_centered(
            draw, (HEIGHT - 3 * (96 + 20)) // 2, hook_text, title_font, WHITE, 920
        )

    # Swipe prompt at bottom
    swipe_font = _load_font(36, bold=True)
    swipe_text = "Swipe →"
    bbox = swipe_font.getbbox(swipe_text)
    w = bbox[2] - bbox[0]
    draw.text(((WIDTH - w) / 2, HEIGHT - 120), swipe_text, font=swipe_font, fill=ORANGE)

    _draw_prpath_watermark(img, draw)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, "PNG", optimize=True)
    return output_path


def numbered_slide(
    num: int,
    title: str,
    body: str = None,
    total: int = None,
    bg_image_path: str = None,
    output_path: str = None,
) -> str:
    """
    Numbered listicle slide — "1. You don't remember last week's numbers"

    Used in D1 Numbered Mistake List format.
    """
    if bg_image_path and Path(bg_image_path).exists():
        img = Image.open(bg_image_path).convert("RGB")
        img = img.resize((WIDTH, HEIGHT))
        img = _apply_dark_overlay(img, opacity=0.75)
    else:
        img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)

    draw = ImageDraw.Draw(img)

    # Big number top left
    num_font = _load_font(200, bold=True)
    num_text = str(num)
    if total:
        # Show as "1/5"
        small_total_font = _load_font(80, bold=True)
        total_text = f"/{total}"
        num_bbox = num_font.getbbox(num_text)
        total_bbox = small_total_font.getbbox(total_text)

        draw.text((80, 120), num_text, font=num_font, fill=ORANGE)
        draw.text(
            (80 + num_bbox[2] + 10, 120 + num_bbox[3] - total_bbox[3] - 20),
            total_text, font=small_total_font, fill=GRAY
        )
    else:
        draw.text((80, 120), num_text, font=num_font, fill=ORANGE)

    # Title (big bold)
    title_font = _load_font(72, bold=True)
    title_lines = _wrap_text(title, title_font, 920)
    y = 420
    for line in title_lines:
        draw.text((80, y), line, font=title_font, fill=WHITE)
        y += 82

    # Body text (optional, smaller)
    if body:
        body_font = _load_font(40, bold=False)
        body_lines = _wrap_text(body, body_font, 920)
        y += 40
        for line in body_lines:
            draw.text((80, y), line, font=body_font, fill=GRAY_LIGHT)
            y += 52

    _draw_prpath_watermark(img, draw)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, "PNG", optimize=True)
    return output_path


def text_slide(
    title: str,
    subtitle: str = None,
    bg_image_path: str = None,
    accent_color: tuple = None,
    output_path: str = None,
) -> str:
    """
    Simple bold text slide — statement + optional subtitle.
    Used for relatable gym memes and strong statements.
    """
    if bg_image_path and Path(bg_image_path).exists():
        img = Image.open(bg_image_path).convert("RGB")
        img = img.resize((WIDTH, HEIGHT))
        img = _apply_dark_overlay(img, opacity=0.7)
    else:
        img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)

    draw = ImageDraw.Draw(img)
    color = accent_color if accent_color else WHITE

    # Centered bold title
    title_font = _load_font(88, bold=True)
    lines = _wrap_text(title, title_font, 960)
    total_h = len(lines) * 98
    if subtitle:
        total_h += 80

    y = (HEIGHT - total_h) // 2
    for line in lines:
        bbox = title_font.getbbox(line)
        w = bbox[2] - bbox[0]
        draw.text(((WIDTH - w) / 2, y), line, font=title_font, fill=color)
        y += 98

    if subtitle:
        y += 30
        sub_font = _load_font(42, bold=False)
        sub_lines = _wrap_text(subtitle, sub_font, 920)
        for line in sub_lines:
            bbox = sub_font.getbbox(line)
            w = bbox[2] - bbox[0]
            draw.text(((WIDTH - w) / 2, y), line, font=sub_font, fill=GRAY_LIGHT)
            y += 52

    _draw_prpath_watermark(img, draw)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, "PNG", optimize=True)
    return output_path


def comparison_slide(
    left_label: str,
    left_text: str,
    right_label: str,
    right_text: str,
    left_color: tuple = None,
    right_color: tuple = None,
    output_path: str = None,
) -> str:
    """
    Side-by-side comparison slide.
    "Do This / Not That" format.
    Red bad / green good color coding.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)
    draw = ImageDraw.Draw(img)

    left_color = left_color or TIER_COLORS["Beginner"]   # red
    right_color = right_color or TIER_COLORS["Advanced"] # green

    # Draw vertical divider
    draw.line([(WIDTH // 2, 100), (WIDTH // 2, HEIGHT - 100)], fill=(80, 80, 85), width=2)

    # Labels at top
    label_font = _load_font(60, bold=True)
    bbox_l = label_font.getbbox(left_label)
    bbox_r = label_font.getbbox(right_label)

    left_center_x = WIDTH // 4
    right_center_x = 3 * WIDTH // 4

    draw.text(
        (left_center_x - (bbox_l[2] - bbox_l[0]) // 2, 180),
        left_label, font=label_font, fill=left_color,
    )
    draw.text(
        (right_center_x - (bbox_r[2] - bbox_r[0]) // 2, 180),
        right_label, font=label_font, fill=right_color,
    )

    # Body text on each side
    body_font = _load_font(48, bold=True)
    left_lines = _wrap_text(left_text, body_font, 440)
    right_lines = _wrap_text(right_text, body_font, 440)

    y = 420
    for line in left_lines:
        bbox = body_font.getbbox(line)
        draw.text(
            (left_center_x - (bbox[2] - bbox[0]) // 2, y),
            line, font=body_font, fill=WHITE,
        )
        y += 60

    y = 420
    for line in right_lines:
        bbox = body_font.getbbox(line)
        draw.text(
            (right_center_x - (bbox[2] - bbox[0]) // 2, y),
            line, font=body_font, fill=WHITE,
        )
        y += 60

    _draw_prpath_watermark(img, draw)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, "PNG", optimize=True)
    return output_path


def stat_shock_slide(
    big_stat: str,
    context_line: str = None,
    subtitle: str = None,
    output_path: str = None,
) -> str:
    """
    Big stat reveal slide. "1% of lifters", "85/100", "10x the progress".
    Used for dramatic number reveals in listicle carousels.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_DARK)
    draw = ImageDraw.Draw(img)

    # Optional context line above
    if context_line:
        context_font = _load_font(44, bold=True)
        bbox = context_font.getbbox(context_line)
        w = bbox[2] - bbox[0]
        draw.text(((WIDTH - w) / 2, 350), context_line, font=context_font, fill=GRAY_LIGHT)

    # Big stat — HUGE
    stat_font = _load_font(260, bold=True)
    lines = _wrap_text(big_stat, stat_font, 1000)
    y = HEIGHT // 2 - 130
    for line in lines:
        bbox = stat_font.getbbox(line)
        w = bbox[2] - bbox[0]
        draw.text(((WIDTH - w) / 2, y), line, font=stat_font, fill=ORANGE)
        y += 260

    # Subtitle below
    if subtitle:
        sub_font = _load_font(48, bold=True)
        sub_lines = _wrap_text(subtitle, sub_font, 900)
        y = HEIGHT - 350
        for line in sub_lines:
            bbox = sub_font.getbbox(line)
            w = bbox[2] - bbox[0]
            draw.text(((WIDTH - w) / 2, y), line, font=sub_font, fill=WHITE)
            y += 60

    _draw_prpath_watermark(img, draw)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, "PNG", optimize=True)
    return output_path


# ─── CLI Test ────────────────────────────────────────────────

if __name__ == "__main__":
    out = Path(__file__).parent / "slides" / "pillow_test"
    out.mkdir(parents=True, exist_ok=True)

    print("Testing Pillow slide renderer...")

    # Cover hook
    cover_hook_slide(
        hook_text="5 REASONS you're stuck at the same weight",
        accent_word="REASONS",
        output_path=str(out / "01_cover.png"),
    )
    print("  01_cover.png")

    # Numbered slides
    for i, (title, body) in enumerate([
        ("You don't remember last week's numbers", "Your brain forgets fast. The app doesn't."),
        ("You're not tracking progressive overload", "If you're not adding weight, you're not growing."),
        ("Your rest times are inconsistent", "3 min rest vs 1 min rest = different workouts."),
        ("You never deload when you should", "Progress comes from stress + recovery, not just stress."),
        ("You're guessing instead of measuring", "Data tells the truth. Feelings lie."),
    ], 1):
        numbered_slide(
            num=i, title=title, body=body, total=5,
            output_path=str(out / f"0{i+1}_num.png"),
        )
        print(f"  0{i+1}_num.png")

    # Comparison slide
    comparison_slide(
        left_label="NOT THIS", left_text="Benching the same weight every Monday",
        right_label="DO THIS", right_text="Track every set and add 2.5 lbs weekly",
        output_path=str(out / "07_compare.png"),
    )
    print("  07_compare.png")

    # Stat shock slide
    stat_shock_slide(
        big_stat="1%",
        context_line="Only",
        subtitle="of lifters reach World Class on squat",
        output_path=str(out / "08_stat.png"),
    )
    print("  08_stat.png")

    # Text slide (for memes/statements)
    text_slide(
        title="If you're not tracking it,",
        subtitle="it didn't happen.",
        output_path=str(out / "09_text.png"),
    )
    print("  09_text.png")

    print(f"\nAll test slides in: {out}")
