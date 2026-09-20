import re
from pathlib import Path

html_path = Path("docs/architecture.html")
svg_path = Path("docs/architecture-diagram.svg")

html = html_path.read_text(encoding="utf-8")
match = re.search(r'(<svg\b[^>]*role="img"[^>]*>.*?</svg>)', html, re.DOTALL)

if match:
    svg_content = match.group(1)
    # Ensure XML namespace is present for standalone SVG
    if 'xmlns="http://www.w3.org/2000/svg"' not in svg_content:
        svg_content = svg_content.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ')
    
    # Also grab the diagram style block if exists
    style_match = re.search(r'(<style\b[^>]*>.*?</style>)', html, re.DOTALL)
    if style_match and "<style" not in svg_content:
        # Insert style right after <svg ...>
        first_gt = svg_content.find(">")
        svg_content = svg_content[:first_gt+1] + "\n" + style_match.group(1) + svg_content[first_gt+1:]
        
    svg_path.write_text(svg_content, encoding="utf-8")
    print(f"SVG successfully extracted to {svg_path} ({len(svg_content)} bytes)")
else:
    print("Error: Could not find <svg role='img'> in HTML.")

