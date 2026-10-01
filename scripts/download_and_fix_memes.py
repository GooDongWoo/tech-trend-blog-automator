import re
import sys
from pathlib import Path
import httpx

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

blog_repo_path = Path(r"c:\Users\dongwoo\vs_proj\GooDongWoo.github.io")
memes_dir = blog_repo_path / "assets" / "images" / "memes"
memes_dir.mkdir(parents=True, exist_ok=True)

# 1. Verified working GIF sources to download locally
MEME_SOURCES = {
    "works-on-my-machine.gif": "https://media.giphy.com/media/9K2nFglCAQClO/giphy.gif",
    "hotfix-in-production.gif": "https://media.giphy.com/media/13HgwGsXF0aiGY/giphy.gif",
    "github-star.gif": "https://media.giphy.com/media/26AHPxxnSw1L9T1rW/giphy.gif",
    "this-is-fine.gif": "https://media.giphy.com/media/NTur7XlVDUdqM/giphy.gif",
    "rage-computer-throw.gif": "https://media.giphy.com/media/11tTNkNy1SdXGg/giphy.gif",
    "mind-blown.gif": "https://media.giphy.com/media/26ufdipQqU2lhNA4g/giphy.gif",
    "confused-travolta.gif": "https://media.giphy.com/media/g01ZnwAUvutuK8GIQn/giphy.gif",
    "legacy-dumpster.gif": "https://media.giphy.com/media/NdKVEei95yvIY/giphy.gif"
}

print("=" * 60)
print("1. Downloading high-quality developer meme GIFs locally...")
print("=" * 60)

with httpx.Client(timeout=15.0, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
    for filename, url in MEME_SOURCES.items():
        target_file = memes_dir / filename
        if not target_file.exists() or target_file.stat().st_size == 0:
            print(f"Downloading {filename} from {url}...")
            r = client.get(url)
            if r.status_code == 200 and len(r.content) != 239321:
                target_file.write_bytes(r.content)
                print(f" -> Saved {filename} ({len(r.content):,} bytes)")
            else:
                print(f" -> FAILED to download {filename} (status={r.status_code}, len={len(r.content)})")
        else:
            print(f" -> Already exists: {filename} ({target_file.stat().st_size:,} bytes)")

# 2. URL mapping from dead/hotlinked Giphy URLs to reliable local paths
URL_MAP = {
    # Dead / "Not Available" Giphy URLs
    "https://media.giphy.com/media/unQ3IJU2RG7DO/giphy.gif": "/assets/images/memes/rage-computer-throw.gif",
    "https://media.giphy.com/media/QMHoU66sBXCAU/giphy.gif": "/assets/images/memes/this-is-fine.gif",
    "https://media.giphy.com/media/dhg2WApHqu7osn9SlJ/giphy.gif": "/assets/images/memes/mind-blown.gif",
    # Live Giphy URLs -> convert to local for 100% resilience & performance
    "https://media.giphy.com/media/9K2nFglCAQClO/giphy.gif": "/assets/images/memes/works-on-my-machine.gif",
    "https://media.giphy.com/media/13HgwGsXF0aiGY/giphy.gif": "/assets/images/memes/hotfix-in-production.gif",
    "https://media.giphy.com/media/26AHPxxnSw1L9T1rW/giphy.gif": "/assets/images/memes/github-star.gif",
}

print("\n" + "=" * 60)
print("2. Replacing all meme links across all blog posts...")
print("=" * 60)

posts_dir = blog_repo_path / "_posts"
modified_count = 0
for md_file in sorted(posts_dir.glob("*.md")):
    content = md_file.read_text(encoding="utf-8")
    original = content
    for old_url, new_url in URL_MAP.items():
        content = content.replace(old_url, new_url)
    
    if content != original:
        md_file.write_text(content, encoding="utf-8")
        modified_count += 1
        print(f"✅ Updated: {md_file.name}")
    else:
        print(f"ℹ️ No change needed: {md_file.name}")

print(f"\n🎉 Total {modified_count} posts updated with permanent local meme assets!")
