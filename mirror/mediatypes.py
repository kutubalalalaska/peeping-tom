"""The ONE media-type classifier. Every module that needs to know what a media
file IS (decode routing, transcript labels, the decodable set) asks here —
previously this logic lived in three separate places and drifted.

Classification is by WhatsApp's name tags (PHOTO/AUDIO/VIDEO/STICKER embedded in
export filenames) plus extension, so it works on a bare filename string before
the file is ever opened.
"""

from pathlib import Path

DECODABLE = {"image", "sticker", "audio", "video"}

_AUDIO_EXT = {".opus", ".m4a", ".mp3", ".wav", ".ogg"}
_VIDEO_EXT = {".mp4", ".mov", ".3gp", ".webm", ".gif"}
# .tgs = Telegram's gzipped-Lottie animated sticker (no raster a VLM can read);
# captioned upstream from the message emoji and never sent to the VLM.
_STICKER_EXT = {".webp", ".tgs"}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif"}


def kind(f) -> str:
    """image | sticker | audio | video | document — from a Path or filename."""
    p = Path(str(f))
    n, e = p.name.upper(), p.suffix.lower()
    if "AUDIO" in n or e in _AUDIO_EXT:
        return "audio"
    if "VIDEO" in n or "GIF" in n or e in _VIDEO_EXT:
        return "video"
    if e in _STICKER_EXT or "STICKER" in n:
        return "sticker"
    if "PHOTO" in n or "IMAGE" in n or e in _IMAGE_EXT:
        return "image"
    return "document"


def is_video_note(f) -> bool:
    """A round video MESSAGE (Telegram keeps these under round_video_messages/),
    as opposed to a shared clip — speech-first, always worth transcribing."""
    s = str(f).lower()
    return "round_video" in s or "video_message" in s
