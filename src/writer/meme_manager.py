"""Compatibility adapter; verified editorial media replaces random captions."""
from pathlib import Path

from src.editorial.media import MediaChoice, render_media


class MemeManager:
    """Formatting only. Construction never copies assets or writes a blog repo."""

    def __init__(self, blog_repo_path: Path | None = None):
        self.blog_repo_path = blog_repo_path

    def format_meme_markdown(self, meme: MediaChoice) -> str:
        return render_media(meme)
