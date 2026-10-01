import random
import shutil
from pathlib import Path
from typing import List, Dict

from config import settings


class MemeManager:
    """Manages tech memes, humor illustrations, and visual assets for blog posts."""

    # Curated safe, popular developer memes & gifs (stored locally in /assets/images/memes/)
    DEV_MEMES = [
        {
            "caption": "코드가 왜 돌아가는지 아무도 모를 때",
            "url": "/assets/images/memes/mind-blown.gif"
        },
        {
            "caption": "분명 로컬에선 잘 돌아갔는데...?",
            "url": "/assets/images/memes/works-on-my-machine.gif"
        },
        {
            "caption": "기술 스택을 새로 도입하기 전과 도입한 후의 모습",
            "url": "/assets/images/memes/this-is-fine.gif"
        },
        {
            "caption": "릴리즈 5분 전 긴급 핫픽스 상황",
            "url": "/assets/images/memes/hotfix-in-production.gif"
        },
        {
            "caption": "메모리 누수 잡으려다가 OS까지 날려먹을 뻔한 개발자",
            "url": "/assets/images/memes/rage-computer-throw.gif"
        },
        {
            "caption": "새로운 오픈소스 라이브러리 스타 찍는 손가락",
            "url": "/assets/images/memes/github-star.gif"
        },
        {
            "caption": "문서와 실제 구현 코드가 완전히 다를 때",
            "url": "/assets/images/memes/confused-travolta.gif"
        },
        {
            "caption": "수년 전 작성된 레거시 코드를 처음 열어봤을 때",
            "url": "/assets/images/memes/legacy-dumpster.gif"
        }
    ]

    def __init__(self, blog_repo_path: Path | None = None):
        self.blog_repo_path = blog_repo_path or settings.blog_repo_path
        self.images_dir = self.blog_repo_path / "assets" / "images" / "posts"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.memes_dir = self.blog_repo_path / "assets" / "images" / "memes"
        self.memes_dir.mkdir(parents=True, exist_ok=True)
        self._sync_meme_assets()

    def _sync_meme_assets(self):
        """Ensure local meme assets are present in the Jekyll blog repo."""
        local_assets_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "memes"
        if local_assets_dir.exists():
            for src_file in local_assets_dir.glob("*.gif"):
                dst_file = self.memes_dir / src_file.name
                if not dst_file.exists() or dst_file.stat().st_size == 0:
                    shutil.copy2(src_file, dst_file)

    def get_random_meme(self) -> Dict[str, str]:
        """Return a random developer meme with caption."""
        return random.choice(self.DEV_MEMES)

    def format_meme_markdown(self, meme: Dict[str, str]) -> str:
        """Format a meme as markdown."""
        return f"\n\n![{meme['caption']}]({meme['url']})\n*▲ {meme['caption']}*\n\n"
