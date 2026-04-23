"""
Video Stitcher — Converts carousel PNGs into a 9:16 slideshow video.

Uses FFmpeg to:
1. Take carousel PNGs (1080x1350, 4:5)
2. Letterbox them into 1080x1920 (9:16) with branded top/bottom bars
3. Hold each slide for ~3.5 seconds
4. Crossfade transitions between slides
5. Output MP4

Usage:
    python -X utf8 video_stitcher.py slides/gen_000/gen000_01_squat_180
    python -X utf8 video_stitcher.py --all slides/gen_000
"""

import subprocess
import sys
import argparse
import random
from pathlib import Path


SLIDE_DURATION = 3.0  # seconds per slide (GENESIS can evolve this later)
FADE_DURATION = 0.5   # crossfade duration
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
# Letterbox: 1080x1350 image centered in 1080x1920 = 285px padding top and bottom
PAD_TOP = 285
PAD_BOTTOM = 285

MUSIC_DIR = Path(__file__).parent / "assets" / "music"
MUSIC_VOLUME = 0.5  # 50% volume — not overpowering


def pick_music(genre: str = None) -> Path:
    """Pick a random track from the music library, optionally filtered by genre.
    Genres: chill, hype, upbeat. None = any."""
    if not MUSIC_DIR.exists():
        return None
    tracks = list(MUSIC_DIR.glob("*.mp3"))
    if genre:
        tracks = [t for t in tracks if t.stem.startswith(genre)]
    if not tracks:
        return None
    return random.choice(tracks)


def stitch_video(
    slides_dir: str,
    output_path: str = None,
    music_genre: str = "hype",
    music_path: str = None,
    transition: str = "fade",
    slide_duration: float = None,
) -> str:
    """Convert a folder of PNGs into a 9:16 MP4 slideshow with background music.

    Args:
        slides_dir: Folder containing slide_*.png files
        output_path: Where to save the MP4
        music_genre: 'hype', 'upbeat', 'chill', or None (no music / any via pick_music(None))
        music_path: Specific track override (full path)
        transition: 'fade' (default — crossfade between slides) or 'cut' (abrupt)
        slide_duration: Seconds per slide. Default uses SLIDE_DURATION (3.0).
    """
    slides_dir = Path(slides_dir)
    slides = sorted(slides_dir.glob("slide_*.png"))

    if not slides:
        print(f"[error] No slides found in {slides_dir}")
        return None

    if not output_path:
        output_path = str(slides_dir / f"{slides_dir.name}.mp4")

    slide_dur = float(slide_duration) if slide_duration is not None else SLIDE_DURATION
    fade_dur = FADE_DURATION if transition == "fade" else 0.0
    n = len(slides)
    total_duration = n * slide_dur - fade_dur * (n - 1) if n > 1 else slide_dur

    # Pick music
    music_file = None
    if music_path:
        music_file = Path(music_path)
    elif music_genre:
        music_file = pick_music(music_genre)

    print(f"[video] Stitching {n} slides into {total_duration:.1f}s video...")
    print(f"  Input: {slides_dir}")
    print(f"  Output: {output_path}")
    print(f"  Format: {OUTPUT_WIDTH}x{OUTPUT_HEIGHT} (9:16), {slide_dur}s/slide, transition={transition}")
    if music_file:
        print(f"  Music: {music_file.name} @ {int(MUSIC_VOLUME * 100)}% volume")
    else:
        print(f"  Music: none")

    # Build FFmpeg filter chain
    # Each slide: load → scale to fit → pad to 1080x1920 with dark background → set duration
    inputs = []
    filter_parts = []

    for i, slide in enumerate(slides):
        inputs.extend(["-loop", "1", "-t", str(slide_dur), "-i", str(slide)])
        # Scale preserving aspect ratio into a 1080x1920 box (decrease if larger),
        # then pad to exactly 1080x1920 with dark letterbox. Fixed 2026-04-22 —
        # handles mixed-aspect source slides (1080x1350 designed carousels vs
        # 1206x2622 iPhone captures).
        filter_parts.append(
            f"[{i}:v]scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=#1C1C1E,setsar=1[v{i}]"
        )

    # Build transition chain — fade (crossfade) or cut (concat)
    if n == 1:
        filter_chain = filter_parts[0].replace(f"[v0]", "[outv]")
    elif transition == "cut":
        # Straight concat — no crossfade. Each slide plays full duration then hard-cut.
        concat_inputs = "".join(f"[v{i}]" for i in range(n))
        filter_chain = ";".join(filter_parts + [f"{concat_inputs}concat=n={n}:v=1:a=0[outv]"])
    else:
        # Default: fade (xfade crossfade chain)
        xfade_parts = []
        prev = "v0"
        for i in range(1, n):
            offset = i * slide_dur - fade_dur * i
            out_label = "outv" if i == n - 1 else f"xf{i}"
            xfade_parts.append(
                f"[{prev}][v{i}]xfade=transition=fade:duration={fade_dur}:offset={offset:.2f}[{out_label}]"
            )
            prev = out_label

        filter_chain = ";".join(filter_parts + xfade_parts)

    # Add audio input + processing if music available
    audio_map = []
    if music_file and music_file.exists():
        inputs.extend(["-i", str(music_file)])
        music_idx = n  # Audio input is the next index after all video inputs
        # Audio filter: set volume, trim to video duration, fade out at end
        fade_start = max(0, total_duration - 1.5)
        filter_chain += (
            f";[{music_idx}:a]volume={MUSIC_VOLUME},"
            f"atrim=0:{total_duration:.2f},"
            f"afade=t=out:st={fade_start:.2f}:d=1.5[outa]"
        )
        audio_map = ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k", "-shortest"]

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_chain,
        "-map", "[outv]",
        *audio_map,
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-r", "30",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"[error] FFmpeg failed:\n{result.stderr[-500:]}", file=sys.stderr)
        return None

    output_size = Path(output_path).stat().st_size / 1024
    print(f"[ok] Video created: {output_path} ({output_size:.0f} KB)")
    return output_path


def stitch_all(gen_dir: str, music_genre: str = "hype"):
    """Stitch videos for all carousels in a generation directory."""
    gen_dir = Path(gen_dir)
    for subdir in sorted(gen_dir.iterdir()):
        if subdir.is_dir() and subdir.name.startswith("gen0"):
            print(f"\n{'='*50}")
            stitch_video(str(subdir), music_genre=music_genre)


def main():
    parser = argparse.ArgumentParser(description="Stitch carousel PNGs into 9:16 video")
    parser.add_argument("path", help="Slides directory or generation directory with --all")
    parser.add_argument("--all", action="store_true", help="Stitch all carousels in directory")
    parser.add_argument("--output", "-o", type=str, help="Output path override")
    parser.add_argument("--music", type=str, default="hype", choices=["hype", "upbeat", "chill", "none"],
                        help="Music genre (default: hype)")
    parser.add_argument("--music-file", type=str, help="Specific music track path (overrides genre)")
    args = parser.parse_args()

    music_genre = None if args.music == "none" else args.music

    if args.all:
        stitch_all(args.path, music_genre=music_genre)
    else:
        stitch_video(args.path, args.output, music_genre=music_genre, music_path=args.music_file)


if __name__ == "__main__":
    main()
