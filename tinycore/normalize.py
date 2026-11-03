"""
tinycore/normalize.py
Cleans HTML, strips transient noise (dates, timestamps, tracking parameters, banners),
and segments text into structured content Blocks with normalized hashes.
"""

import re
import hashlib
from dataclasses import dataclass
from bs4 import BeautifulSoup, Comment

# Regex patterns for transient noise
NOISE_PATTERNS = [
    # ISO timestamps and common dates
    r"\b\d{4}-\d{2}-\d{2}(?:T|\s+)\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    # Relative time strings
    r"\b\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago\b",
    r"\bjust now\b",
    # Last updated / modified lines
    r"(?i)\blast\s+(?:updated|modified|reviewed|checked)\s*[:\-]?\s*.*?(?:\n|$)",
    # Copyright notices and years
    r"(?i)(?:©|\bcopyright\b)\s*(?:\d{4}\s*[-–]\s*)?\d{4}\b.*?(?:\n|$)",
    # UUIDs
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    # Tracking parameters in URLs
    r"(?:[\?&])(?:utm_[a-z]+|gclid|fbclid|ref|session_id|sid)=[^&\s)]+",
    # CSRF tokens, nonces, hex hashes (32+ chars)
    r"\b(?:csrf[_-]?token|nonce)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]+['\"]?",
    r"\b[0-9a-f]{32,64}\b",
    # View counters / visitor counters
    r"(?i)\b(?:viewed\s+)?\d[\d,]*\s+(?:views?|visitors?|reads?|times?)\b"
]

COMPILED_NOISE = [re.compile(p, re.IGNORECASE) for p in NOISE_PATTERNS]


@dataclass
class Block:
    id: str
    heading_path: str
    text: str
    norm_hash: str


def strip_noise(text: str) -> str:
    """Removes dates, times, tracking tokens, and transient timestamps from text."""
    clean = text
    for pattern in COMPILED_NOISE:
        clean = pattern.sub(" ", clean)
    # Collapse excess whitespace
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r"\n\s*\n+", "\n\n", clean)
    return clean.strip()


def compute_norm_hash(text: str) -> str:
    """Computes a SHA256 hash of noise-stripped, lowercased content."""
    normalized = strip_noise(text).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def clean_html(html: str) -> str:
    """Strips boilerplate, navigation, cookie banners, script/style, and ads from raw HTML."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content tags
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "svg", "iframe"]):
        tag.decompose()

    # Remove comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Remove typical boilerplate elements by class / id keyword
    noise_keywords = ["cookie", "banner", "consent", "gdpr", "modal", "popup", "ad-", "ads-", "advertisement"]
    for el in soup.find_all(True):
        attrs = " ".join([
            str(el.get("id", "")),
            " ".join(el.get("class", []) if isinstance(el.get("class"), list) else [str(el.get("class", ""))])
        ]).lower()
        if any(k in attrs for k in noise_keywords):
            el.decompose()

    # Convert to markdown-like readable text
    lines = []
    for element in soup.descendants:
        if element.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            lvl = int(element.name[1])
            heading_txt = element.get_text(strip=True)
            if heading_txt:
                lines.append(f"\n{'#' * lvl} {heading_txt}\n")
        elif element.name == "li":
            li_txt = element.get_text(strip=True)
            if li_txt:
                lines.append(f"* {li_txt}")
        elif element.name in ["p", "div"] and not element.find(["p", "div", "h1", "h2", "h3", "li"]):
            p_txt = element.get_text(strip=True)
            if p_txt:
                lines.append(p_txt)

    raw_text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", raw_text).strip()


def to_blocks(text: str) -> list[Block]:
    """Segments markdown or formatted text into structured Blocks by heading path."""
    blocks: list[Block] = []
    lines = text.splitlines()
    
    current_headings = []
    current_block_lines = []

    def flush_block():
        nonlocal current_block_lines
        content = "\n".join(current_block_lines).strip()
        if content:
            path = " > ".join(current_headings) if current_headings else "root"
            norm_hash = compute_norm_hash(content)
            block_id = hashlib.sha256(f"{path}:{norm_hash}".encode("utf-8")).hexdigest()[:16]
            blocks.append(Block(id=block_id, heading_path=path, text=content, norm_hash=norm_hash))
        current_block_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_block_lines:
                flush_block()
            continue

        # Check heading
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush_block()
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            
            # Adjust heading hierarchy
            while len(current_headings) >= level:
                current_headings.pop()
            current_headings.append(heading_text)
            continue

        current_block_lines.append(stripped)

    flush_block()
    return blocks
