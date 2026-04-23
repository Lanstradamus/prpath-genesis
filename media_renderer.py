"""Media rendering strategies for the PRPath Content pipeline.

A MediaRenderer turns a slot dict into the upload-ready assets PFM needs.
Each slot picks a renderer by its `media_kind` field:

  - 'carousel' → SlideCarouselRenderer: reads slide_01_path + slide_02_path
                 from the slot, stitches them into a short MP4 (for IG/FB/YT
                 reels), and returns both the PNGs (for TikTok carousel) and
                 the MP4. This is PRPath's existing flow.
  - 'video'    → RemotionVideoRenderer: stub. LaunchLens either (a) passes a
                 pre-rendered MP4 path in `slot['media_paths'][0]`, or (b)
                 subclasses this renderer to invoke Remotion inline.

Adding a new renderer: implement the MediaRenderer Protocol and register it
in `renderer_for_slot()`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class RenderedMedia:
    """Assets ready for PFM upload.

    video_mp4     — path to the MP4 used by IG/FB/YT (and by TikTok when no
                    carousel is available). None if the slot has no video.
    carousel_pngs — ordered list of PNG paths for a TikTok photo carousel.
                    Empty list for video-only flows.
    """
    video_mp4: Path | None = None
    carousel_pngs: list[Path] = field(default_factory=list)


class MediaRenderer(Protocol):
    def render_for_slot(self, slot: dict[str, Any]) -> RenderedMedia:
        ...


class SlideCarouselRenderer:
    """PRPath flow: two slide PNGs → carousel + stitched MP4 for reels."""

    def render_for_slot(self, slot: dict[str, Any]) -> RenderedMedia:
        # Deferred import so importing media_renderer doesn't pull in ffmpeg
        # wrappers on platforms that never stitch (e.g. LaunchLens's Windows).
        from postforme_client import stitch_slides_to_video

        slide_01 = Path(slot["slide_01_path"]).resolve()
        slide_02 = Path(slot["slide_02_path"]).resolve()
        if not slide_01.is_file() or not slide_02.is_file():
            raise FileNotFoundError(f"slides missing: {slide_01} or {slide_02}")

        post_id = slot.get("post_id") or slot["slot_id"]
        video_out = slide_01.parent / f"{post_id}_short.mp4"
        stitch_slides_to_video(slide_01.parent, video_out)

        return RenderedMedia(
            video_mp4=video_out,
            carousel_pngs=[slide_01, slide_02],
        )


class RemotionVideoRenderer:
    """Video-only flow (LaunchLens). TikTok gets the MP4, not a carousel.

    Two supported modes:

      (a) Pre-rendered — slot['media_paths'][0] points at an already-rendered
          MP4. The renderer just resolves the path and returns it.

      (b) Inline render — subclass this renderer and override `render_for_slot`
          to invoke Remotion (or any other renderer) and return the MP4 path.

    Example (b) sketch:

        class LaunchLensRemotionRenderer(RemotionVideoRenderer):
            def render_for_slot(self, slot):
                mp4 = run_remotion(
                    template=slot['remotion_template'],
                    props=slot['remotion_props'],
                    out_dir=Path('/tmp/ll-renders'),
                )
                return RenderedMedia(video_mp4=mp4)
    """

    def render_for_slot(self, slot: dict[str, Any]) -> RenderedMedia:
        media_paths = slot.get("media_paths") or []
        if media_paths:
            video_path = Path(media_paths[0]).resolve()
            if not video_path.is_file():
                raise FileNotFoundError(f"video not found: {video_path}")
            return RenderedMedia(video_mp4=video_path)
        raise NotImplementedError(
            "RemotionVideoRenderer is a stub. Either include 'media_paths': "
            "['/abs/path/to/render.mp4'] in the slot manifest, or subclass "
            "this renderer to invoke Remotion inline."
        )


def renderer_for_slot(slot: dict[str, Any]) -> MediaRenderer:
    """Factory — pick a renderer based on slot['media_kind']."""
    kind = (slot.get("media_kind") or "carousel").lower()
    if kind == "carousel":
        return SlideCarouselRenderer()
    if kind == "video":
        return RemotionVideoRenderer()
    raise ValueError(f"unknown media_kind: {kind!r}")
