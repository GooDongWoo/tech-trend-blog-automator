import re
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

blog_dir = Path(r"c:\Users\dongwoo\vs_proj\GooDongWoo.github.io")
posts_dir = blog_dir / "_posts"
memes_dir = blog_dir / "assets" / "images" / "memes"

print("=" * 60)
print("1. Checking all blog posts for giphy links or missing local assets...")
print("=" * 60)

giphy_pattern = re.compile(r"https?://[^\s\)\"]*giphy[^\s\)\"]*")
local_meme_pattern = re.compile(r"/assets/images/memes/([a-zA-Z0-9_\-\.]+)")

errors = []
total_memes_referenced = 0

for p in sorted(posts_dir.glob("*.md")):
    content = p.read_text(encoding="utf-8")
    
    # Check for remaining giphy links
    g_links = giphy_pattern.findall(content)
    if g_links:
        errors.append(f"[GIPHY REMAINING] {p.name}: {g_links}")
    
    # Check that referenced local memes actually exist on disk
    local_memes = local_meme_pattern.findall(content)
    for meme_file in local_memes:
        total_memes_referenced += 1
        target_path = memes_dir / meme_file
        if not target_path.exists():
            errors.append(f"[MISSING LOCAL FILE] {p.name} references {meme_file} but it does not exist on disk!")
        elif target_path.stat().st_size == 0:
            errors.append(f"[EMPTY LOCAL FILE] {p.name} references {meme_file} but file size is 0 bytes!")

print(f"Total local meme references found in posts: {total_memes_referenced}")
if errors:
    print(f"FAILED: Found {len(errors)} errors:")
    for err in errors:
        print(f" - {err}")
    sys.exit(1)
else:
    print("SUCCESS: 0 giphy links remaining. All referenced local memes exist and are valid!")

print("=" * 60)
