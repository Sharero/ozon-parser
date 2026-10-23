"""User-maintained exact book titles, independent of seller SKU."""
import re
from pathlib import Path
from .models import normalized


def title_key(title: str, book_author: str = '') -> str:
    parts = title.rsplit('|', 1)
    if len(parts) == 2:
        author = normalized(parts[1])
        if (re.search(r'\bкинг\b', author) and re.search(r'\bстивен\b', author)) or (book_author and set(re.findall(r'\w+', author)) == set(re.findall(r'\w+', normalized(book_author)))):
            title = parts[0]
    return ' '.join(re.findall(r'\w+', normalized(title)))


def load_exclusions(path: Path) -> frozenset[str]:
    # Read once per cycle: editing the file takes effect without restarting.
    titles = []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            key = title_key(line)
            if key:
                titles.append(key)
    return frozenset(titles)
