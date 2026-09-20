import random
from pathlib import Path
from typing import List, Dict

from config import settings


class MemeManager:
    """Manages tech memes, humor illustrations, and visual assets for blog posts."""

    # Curated safe, popular developer memes & gifs
    DEV_MEMES = [
        {
            "caption": "코드가 왜 돌아가는지 아무도 모를 때",
            "url": "https://media.giphy.com/media/unQ3IJU2RG7DO/giphy.gif"
        },
        {
            "caption": "분명 로컬에선 잘 돌아갔는데...?",
            "url": "https://media.giphy.com/media/9K2nFglCAQClO/giphy.gif"
        },
        {
            "caption": "기술 스택을 새로 도입하기 전과 도입한 후의 모습",
            "url": "https://media.giphy.com/media/QMHoU66sBXCAU/giphy.gif"
        },
        {
            "caption": "릴리즈 5분 전 긴급 핫픽스 상황",
            "url": "https://media.giphy.com/media/13HgwGsXF0aiGY/giphy.gif"
        },
        {
            "caption": "메모리 누수 잡으려다가 OS까지 날려먹을 뻔한 개발자",
            "url": "https://media.giphy.com/media/dhg2WApHqu7osn9SlJ/giphy.gif"
        },
        {
            "caption": "새로운 오픈소스 라이브러리 스타 찍는 손가락",
            "url": "https://media.giphy.com/media/26AHPxxnSw1L9T1rW/giphy.gif"
        }
    ]

    def __init__(self, blog_repo_path: Path | None = None):
        self.blog_repo_path = blog_repo_path or settings.blog_repo_path
        self.images_dir = self.blog_repo_path / "assets" / "images" / "posts"
        self.images_dir.mkdir(parents=True, exist_ok=True)

    def get_random_meme(self) -> Dict[str, str]:
        """Return a random developer meme with caption."""
        return random.choice(self.DEV_MEMES)

    def format_meme_markdown(self, meme: Dict[str, str]) -> str:
        """Format a meme as markdown."""
        return f"\n\n![{meme['caption']}]({meme['url']})\n*▲ {meme['caption']}*\n\n"
