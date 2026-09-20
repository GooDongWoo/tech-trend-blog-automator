import subprocess
from pathlib import Path
from typing import Dict, Any

from config import settings


class GitPublisher:
    """Manages git commit and push for GooDongWoo.github.io."""

    def __init__(self, repo_path: Path | None = None):
        self.repo_path = Path(repo_path or settings.blog_repo_path)

    def publish(self, file_path: str, title: str) -> Dict[str, Any]:
        """Commit and push new post to GitHub Pages repository."""
        path = Path(file_path)
        rel_path = path.relative_to(self.repo_path) if self.repo_path in path.parents else path.name

        try:
            # 1. git add
            add_res = subprocess.run(
                ["git", "add", str(rel_path)],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            # 2. git commit
            commit_msg = f"feat(blog): publish post '{title}'"
            commit_res = subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            # 3. git push
            push_res = subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            return {
                "success": True,
                "message": f"성공적으로 커밋 및 푸시되었습니다. ({commit_msg})",
                "commit_output": commit_res.stdout,
                "push_output": push_res.stdout
            }
        except subprocess.CalledProcessError as e:
            err = e.stderr or e.stdout or str(e)
            print(f"[GitPublisher] Git command failed: {err}")
            return {
                "success": False,
                "message": f"Git 배포 실패: {err}"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"예외 발생: {e}"
            }
