#!/usr/bin/env python3
# python .\script.py .\input.txt --output courses.md
"""
Usage:
    python3 "data/INTI International College/script.py" \
      "data/INTI International College/input.txt" \
      --output "data/INTI International College/Courses.md"

"""

import argparse
import re
import time
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests
from collections import defaultdict

# --- Program title cleanup ---
SLUG_RENAMES = {
    'foundationinbusiness2': 'Foundation in Business',
    'foundationinbusiness': 'Foundation in Business',
}

SLUG_HEADING_OVERRIDES = {
    "foundationinbusiness2": "foundation-in-business",
    "foundationinbusiness": "foundation-in-business",
}

def heading_from_slug(course: dict) -> str:
    """Return the heading text for '## ...' in markdown."""
    s = (course.get("slug") or "").lower()
    if s in SLUG_HEADING_OVERRIDES:
        return SLUG_HEADING_OVERRIDES[s]
    # Fallback: if we have a prettified name, slugify it (spaces -> hyphens)
    name = course.get("name")
    if name:
        return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return course.get("slug", "")

def prettify_program_name(program_slug: str, default_name: str) -> str:
    key = (program_slug or '').lower()
    if key in SLUG_RENAMES:
        return SLUG_RENAMES[key]
    # Generic tidy: "name-words-2" -> "Name Words"
    n = re.sub(r'[-_]+', ' ', key)       # hyphens/underscores -> spaces
    n = re.sub(r'\d+$', '', n).strip()   # drop trailing digits
    if n:
        return n.title()
    return default_name

def extract_urls_from_html(html_content):
    """Extract all course URLs from the HTML content."""
    soup = BeautifulSoup(html_content, 'html.parser')
    urls = []

    # Find all <a> tags with href containing /programme/
    for link in soup.find_all('a', href=True):
        href = link['href']
        if '/programme/' in href and href not in urls:
            urls.append(href)

    return urls


def _table_to_markdown(rows):
    if not rows:
        return "result not found in online"
    lines = []
    lines.append("| " + " | ".join(rows[0]) + " |")
    lines.append("| " + " | ".join(["---"] * len(rows[0])) + " |")
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def extract_penang_fee_markdown(content_div):
    """
    Returns a Markdown table containing only the 'INTI International College Penang' row.
    Fallbacks to line-based parsing if no HTML table is present.
    """
    target_aliases = [
        "inti international college penang",
        "inti college penang",
        "penang"
    ]

    # ---- A) Prefer real HTML tables ----
    tables = content_div.find_all("table")
    for table in tables:
        # Build rows (use th for header, td for data)
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True).replace("\xa0", " ") for c in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if not rows:
            continue

        header = rows[0]
        lower_header = [h.lower() for h in header]
        # try find campus column
        campus_col = None
        for idx, h in enumerate(lower_header):
            if "campus" in h or "location" in h:
                campus_col = idx
                break
        # if no explicit campus header, assume first column is campus
        if campus_col is None:
            campus_col = 0

        data_rows = rows[1:] if len(rows) > 1 else []
        filtered = []
        for r in data_rows:
            cell = r[campus_col] if campus_col < len(r) else ""
            if any(alias in (cell or "").lower() for alias in target_aliases):
                filtered.append(r)

        if filtered:
            md = _table_to_markdown([header] + filtered)

            # Attach a "Note:" line if present in the fees block
            note_text = None
            for p in content_div.find_all(["p", "div", "span"]):
                t = p.get_text(strip=True)
                if t and t.lower().startswith("note"):
                    note_text = t
                    break
            if note_text:
                md += f"\n\n{note_text}"
            return md

    # ---- B) Fallback: plain text lines (no table on page) ----
    text = extract_text_from_element(content_div)
    lines = [l for l in (text or "").splitlines() if l.strip()]
    penang_idx = next((i for i, l in enumerate(lines) if "penang" in l.lower()), None)

    if penang_idx is not None:
        campus = lines[penang_idx]
        # try grab next two amounts (Local / International)
        amounts = []
        j = penang_idx + 1
        amt_re = re.compile(r"rm\s?[\d,]+", re.I)
        while j < len(lines) and len(amounts) < 2:
            if amt_re.search(lines[j]):
                amounts.append(lines[j])
            j += 1

        header = ["Campus", "Local Students", "International Students"]
        row = [
            campus,
            amounts[0] if len(amounts) > 0 else "-",
            amounts[1] if len(amounts) > 1 else "-",
        ]
        md = _table_to_markdown([header, row])

        note_line = next((l for l in lines if l.lower().startswith("note")), None)
        if note_line:
            md += f"\n\n{note_line}"
        return md

    return "result not found in online"

def extract_penang_intakes_text(content_div):
    """
    From the 'Campus Intakes' block, keep only:
    - 'INTI International College Penang' and its intakes
    - The Note line (if any)
    """
    text = extract_text_from_element(content_div)
    if not text:
        return ""

    import re
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    if not lines:
        return ""

    # Optional heading like "Campuses & Intakes"
    header = None
    start_idx = 0
    if (not re.match(r'(?i)^inti\b', lines[0])
            and not lines[0].lower().startswith("note")):
        header = lines[0]
        start_idx = 1

    # Find Note line (if exists)
    note_idx = next(
        (i for i, l in enumerate(lines) if l.lower().startswith("note")),
        None
    )

    main_end = note_idx if note_idx is not None else len(lines)
    campus_block = lines[start_idx:main_end]

    # Find indices of each campus header (lines starting with "INTI ...")
    campus_header_pat = re.compile(r'(?i)^inti\b')
    idxs = [i for i, l in enumerate(campus_block) if campus_header_pat.match(l)]

    # If the structure isn't as expected, just return original text
    if not idxs:
        return text

    # Slice into campus segments
    segments = []
    for j, rel_start in enumerate(idxs):
        rel_end = idxs[j + 1] if j + 1 < len(idxs) else len(campus_block)
        campus_name = campus_block[rel_start]
        intakes = campus_block[rel_start + 1:rel_end]
        segments.append((campus_name, intakes))

    # Pick the Penang campus
    penang_seg = next(
        (seg for seg in segments if "penang" in seg[0].lower()),
        None
    )
    if not penang_seg:
        # Fallback: no explicit Penang campus found
        return text

    result_lines = []
    if header:
        result_lines.append(header)  # e.g. "Campuses & Intakes"

    result_lines.append(penang_seg[0])      # campus name
    result_lines.extend(penang_seg[1])      # its intakes

    if note_idx is not None:
        result_lines.append(lines[note_idx])  # Note: ...

    return "\n".join(result_lines)

def extract_text_from_element(element):
    """Extract and clean text from a BeautifulSoup element."""
    if not element:
        return ""

    # Get text and clean it up
    text = element.get_text(separator='\n', strip=True)
    # Remove excessive newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def cleanup_programme_structure_text(text: str) -> str:
    if not text:
        return text

    import re
    from typing import List

    # Normalize/split
    raw_lines: List[str] = [re.sub(r'\s+', ' ', l).strip() for l in text.splitlines()]

    chose_for_colleges = False  # track if we selected the Colleges block

    # ---------- 0) Prefer the "For Colleges" block (Penang-targeted) ----------
    fc_idx = -1
    for idx, l in enumerate(raw_lines):
        plain = re.sub(r'^[#*\s]+|[#*\s]+$', '', l or '').strip().rstrip(':').lower()
        if re.fullmatch(r'for\s+college(?:s)?', plain):
            fc_idx = idx
            break

    if fc_idx != -1:
        # keep everything after the "For Colleges" heading (drop the heading itself)
        raw_lines = raw_lines[fc_idx + 1:]
        chose_for_colleges = True

    # keep trailing '*' on subject names (e.g., "English Language Skills 2*")
    _TRAILING_STAR_RE = re.compile(r'(\*+)\s*$')

    def _restore_star_from_raw(original_line: str, cleaned_text: str) -> str:
        if not cleaned_text:
            return cleaned_text
        m = _TRAILING_STAR_RE.search(original_line or '')
        if m and not cleaned_text.endswith('*'):
            # re-attach the full sequence of trailing stars
            return cleaned_text.rstrip() + m.group(1)
        return cleaned_text

    def is_hash_only(s: str) -> bool:
        return bool(s) and re.fullmatch(r'[#\s]+', s) is not None

    def strip_markers(s: str) -> str:
        # remove leading/trailing **, #, *, spaces (but keep inner asterisks like 'A** /')
        return re.sub(r'^[#*\s]+|[#*\s]+$', '', s or '').strip()

    # ---------- 1) Prefer campus = INTI International College Penang (IICP) if blocks exist ----------
    chose_penang = False  # <— track if we already selected a Penang block
    campus_hdr_pat = re.compile(r'(?i)^inti\s+(?:international\s+)?(?:college|university)\b.*$')
    campus_indices = []
    for idx, l in enumerate(raw_lines):
        plain = strip_markers(l)
        if campus_hdr_pat.match(plain):
            campus_indices.append(idx)

    def slice_block(arr: List[str], start: int, end: int | None) -> List[str]:
        seg = arr[start:end] if end is not None else arr[start:]
        return seg[1:] if seg else seg  # drop the campus header line itself

    if campus_indices:
        segments = []
        for j, start in enumerate(campus_indices):
            end = campus_indices[j + 1] if j + 1 < len(campus_indices) else None
            header = strip_markers(raw_lines[start]).lower()
            seg = slice_block(raw_lines, start, end)
            segments.append((header, seg))

        # Prefer the 'penang' segment
        penang_seg = next((seg for header, seg in segments if 'penang' in header), None)
        if penang_seg:
            raw_lines = penang_seg
            chose_penang = True

    # ---------- 2) Fallback: prefer "Other campuses" onward if present (only if Penang not chosen) ----------
    oc_idx = -1
    for idx, l in enumerate(raw_lines):
        plain = strip_markers(l).rstrip(':').lower()
        if re.fullmatch(r'other campuses?', plain):
            oc_idx = idx
            break

    if oc_idx != -1 and not chose_penang and not chose_for_colleges:
        raw_lines = raw_lines[oc_idx:]

    # ---------- 3) Format into headers + bullets ----------
    out: List[str] = []
    i, n = 0, len(raw_lines)

    header_pat = re.compile(
        r'(?i)^(modules|programme structure|academic modules|core modules?|core subjects?|'
        r'mpu\s+subjects?(?:\s*\(.*\))?|compulsory|electives(?:\s*\(.*\))?|select\s+one.*|'
        r'pathway|other campuses|internship(?:\s*\(.*\))?|ibm[-\s]*ice\s+modules|'
        r'concentration streams|general education core|psychology core|'
        r'general psychology concentration|child and adolescent development concentration|'
        r'mental health concentration)$'
    )

    choose_electives_pat = re.compile(r'(?i)choose\s+any\b.*\belective')
    year_level_pat = re.compile(r'''(?ix)
        ^
        (?:year|yr|level|lvl)\s*
        (?:
            (?:\d{1,2}|[ivx]{1,4}|one|two|three|four|five|six|seven|eight|nine|ten)
            (?:\s*(?:&|and|/|-)\s*
               (?:\d{1,2}|[ivx]{1,4}|one|two|three|four|five|six|seven|eight|nine|ten)
            )*
        )
        (?:\s*\(.*\))?
        \s*(?:only)?
        \s*$
    ''')

    numbered_header_pat = re.compile(r'^\s*\d+\.\s*(.+)$')
    skip_repeat_pat = re.compile(r'(?i)^(programme structure|inti international (university|college)(?:\s+penang)?)$')
    footnote_pat = re.compile(
        r'(?i)^(?:\*+.*|only offered.*|prerequisite applies.*|pre-?requisite required.*|'
        r'(?:for\s+malaysian\s+students|for\s+students).*\bspm\s*bm\b.*)$'
    )
    def next_sig_plain(j: int) -> str:
        k = j
        while k < n and (not raw_lines[k] or is_hash_only(raw_lines[k])):
            k += 1
        return strip_markers(raw_lines[k]) if k < n else ''

    while i < n:
        raw = raw_lines[i]
        if not raw or is_hash_only(raw):
            i += 1
            continue

        m_foot = re.match(r'^\s*(\*+)\s*(.+)$', raw)
        if m_foot:
            stars = m_foot.group(1)
            body = m_foot.group(2).strip()
            out.append(f"{stars} {body}")
            i += 1
            continue

        # unwrap "1. Compulsory"
        m = numbered_header_pat.match(raw)
        candidate = m.group(1).strip() if m else raw
        cand_plain = strip_markers(candidate)

        # drop noisy repeaters
        if skip_repeat_pat.match(cand_plain):
            i += 1
            continue

        # normalize inline '## Pathway' at end of line
        cand_plain = re.sub(r'(?i)\s*[#*]+\s*pathway\s*$', ' Pathway', cand_plain).strip(' :')

        # --- Merge "Elective papers for ..." (+ optional next-line "Pathway") ---
        if re.match(r'(?i)^elective papers?\s+for\b', cand_plain):
            if re.search(r'(?i)\bpathway\b', cand_plain):
                out.append(f"**{cand_plain.rstrip(':')}**"); out.append("")
                i += 1; continue
            nxt = next_sig_plain(i + 1).rstrip(':').lower()
            if nxt == 'pathway':
                out.append(f"**{cand_plain} Pathway**"); out.append("")
                # skip forward until after that "Pathway" line
                j = i + 1
                while j < n and (not raw_lines[j] or is_hash_only(raw_lines[j])):
                    j += 1
                i = j + 1; continue
            out.append(f"**{cand_plain}**"); out.append("")
            i += 1; continue

        # --- Explicit "Choose any ... Electives ..." header ---
        if choose_electives_pat.search(cand_plain):
            out.append(f"**{cand_plain.rstrip(':')}**"); out.append("")
            i += 1; continue

        # --- Year/Level headers ---
        if year_level_pat.match(cand_plain):
            out.append(f"**{cand_plain.rstrip(':')}**"); out.append("")
            i += 1; continue

        if re.match(r'^\s*[•●○*]\s+', raw):
            # Special-case: star-led footnotes we want to keep as notes, not list items
            plain_after_star = strip_markers(re.sub(r'^\s*[•●○*]\s+', '', raw))
            if re.search(r'(?i)pre-?requisite\s+(applies|required)', plain_after_star) or \
                    re.search(r'(?i)for\s+malaysian\s+students.*spm\s*bm', plain_after_star):
                if out and out[-1] != "": out.append("")
                out.append("* " + plain_after_star)
                i += 1;
                continue

            nxt_raw = raw_lines[i + 1] if (i + 1) < n else ''
            nxt_sig = next_sig_plain(i + 1)
            if re.match(r'^\s*[-–—]\s*', nxt_raw) or re.match(r'^\s*[-–—]\s*', nxt_sig):
                title = strip_markers(re.sub(r'^\s*[•●○*]\s+', '', raw)).rstrip(' :')
                out.append(f"**{title}**");
                out.append("")
                i += 1;
                continue

            # otherwise treat as a normal subject bullet
            orig_no_bullet = re.sub(r'^\s*[•●○*]\s+', '', raw)
            item = strip_markers(orig_no_bullet)
            item = _restore_star_from_raw(orig_no_bullet, item)
            out.append(f"- {item}")
            i += 1;
            continue

        # --- Hyphen bullets -> subjects ---
        if re.match(r'^\s*[-–—]\s+', raw):
            orig_no_bullet = re.sub(r'^\s*[-–—]\s+', '', raw)
            subj = strip_markers(orig_no_bullet)
            subj = _restore_star_from_raw(orig_no_bullet, subj)


            # 2) Existing: promote some other lines to headers
            if (
                    re.match(r'(?i)^freshman\s*\(year\s*\d+\)\s*at\s*inti\b', subj)
                    or re.match(r'(?i)^sophomore\s*\(year\s*\d+\)\s*at\s*inti\b', subj)
                    or re.match(r'(?i)^sample\s+curriculum\b', subj)
                    or re.match(r'(?i)^professional\s+examination\b', subj)
            ):
                out.append(f"**{subj.rstrip(' :')}**")
                out.append("")
                i += 1
                continue

            # 3) Existing special-cases
            if re.match(r'(?i)^elective\s*\(\s*choose\s*one\s*\)\s*:?\s*$', subj):
                out.append("**Elective (Choose one)**")
                out.append("")
                i += 1
                continue

            # Specialised Modules should also be a bold header
            if (
                    re.search(r'(?i)programme\s*core\s*/\s*areas\s*of\s*concentration', subj)
                    or re.search(r'(?i)specialised\s+modules?', subj)
            ):
                out.append(f"**{subj.rstrip(' :')}**")
                out.append("")
                i += 1
                continue

            # SPM BM footnote lines
            if re.search(r'(?i)\bspm\s*bm\b', subj) and re.search(r'(?i)\bfor\s+students\b', subj):
                if out and out[-1] != "":
                    out.append("")
                out.append("* " + subj)
                i += 1
                continue

            # Normal subject line
            if subj:
                out.append(f"- {subj}")
            i += 1
            continue

        # --- Headers (bold, no bullet) ---
        cand_nocolon = cand_plain[:-1] if cand_plain.endswith(':') else cand_plain
        if header_pat.match(cand_nocolon):
            out.append(f"**{cand_nocolon}**"); out.append("")
            i += 1; continue

        # --- Footnotes / notes (plain) ---
        if footnote_pat.match(cand_plain.lower()):
            # Skip campus-only footnotes entirely
            if re.search(r'(?i)only offered.*penang', cand_plain) or \
                    re.search(r'(?i)only offered.*inti.*international.*university', cand_plain):
                i += 1;
                continue

            if out and out[-1] != "": out.append("")
            note_out = candidate.strip()
            # Ensure both prerequisite and SPM BM notes have a leading '* '
            if not note_out.lstrip().startswith('*') and (
                    re.search(r'(?i)pre-?requisite\s+(applies|required)', cand_plain) or
                    re.search(r'(?i)for\s+malaysian\s+students.*spm\s*bm', cand_plain)
            ):
                note_out = "* " + cand_plain
            out.append(note_out)
            i += 1;
            continue

        # --- Default -> subject bullet ---
        restored = _restore_star_from_raw(raw, cand_plain)

        # NEW: promote specific lines (even if they weren't bullets originally)
        if (
                re.match(r'(?i)^freshman\s*\(year\s*\d+\)\s*at\s*inti\b', cand_plain)
                or re.match(r'(?i)^sophomore\s*\(year\s*\d+\)\s*at\s*inti\b', cand_plain)
                or re.match(r'(?i)^sample\s+curriculum\b', cand_plain)
                or re.match(r'(?i)^professional\s+examination\b', cand_plain)
        ):
            out.append(f"**{cand_plain.rstrip(' :')}**")
            out.append("")
            i += 1
            continue

        # NEW: treat 'Programme core/Areas of concentration' as a header here too
        if (
                re.search(r'(?i)programme\s*core\s*/\s*areas\s*of\s*concentration', cand_plain)
                or re.search(r'(?i)specialised\s+modules?', cand_plain)
        ):
            out.append(f"**{cand_plain.rstrip(' :')}**")
            out.append("")
            i += 1
            continue
        out.append(f"- {restored}")
        i += 1

    # squeeze consecutive blanks
    cleaned, prev_blank = [], False
    for l in out:
        if l == "":
            if not prev_blank: cleaned.append(l)
            prev_blank = True
        else:
            cleaned.append(l); prev_blank = False

    return "\n".join(cleaned).strip()

# --- Duration extractor (robust for INTI meta strip) ---

# First: handle composite patterns like "1+3 or 1.5+2.5 or 2+2 Years"
_COMPLEX_DUR_RE = re.compile(
    r'(?i)\b('
    r'\d[0-9\.\s\+\-–]{0,30}'
    r'(?:\s*(?:or|to)\s*\d[0-9\.\s\+\-–]{0,30})+'
    r')\s*'
    r'(year|years|yr|yrs|month|months|mo|mth|mths)\b'
)

# Fallback: simple single / range duration like "2 Years" or "1–2 Years"
_DUR_RE = re.compile(
    r'(?i)\b(\d+(?:\.\d+)?(?:\s*[-–]\s*\d+(?:\.\d+)?)?)\s*'
    r'(year|years|yr|yrs|month|months|mo|mth|mths)\b'
)

def _format_duration(value: str, unit: str) -> str:
    value = re.sub(r'\s+', ' ', value.replace('–', '-')).strip()
    u = unit.lower()
    if u.startswith(('year', 'yr')):
        unit_fmt = 'Year' if re.fullmatch(r'1(?:\.0+)?', value) else 'Years'
    else:
        unit_fmt = 'Month' if re.fullmatch(r'1(?:\.0+)?', value) else 'Months'
    return f"{value} {unit_fmt}"

def _first_duration(text: str) -> str | None:
    if not text:
        return None

    # 1) Try composite formats first, e.g. "1+3 or 1.5+2.5 or 2+2 Years"
    m = _COMPLEX_DUR_RE.search(text)
    if m:
        return _format_duration(m.group(1), m.group(2))

    # 2) Otherwise fall back to normal "2 Years", "1–2 Years", etc.
    m = _DUR_RE.search(text)
    return _format_duration(m.group(1), m.group(2)) if m else None


def _nearby_elements(el, steps=4):
    # look around siblings and parent for short meta items
    # yields a few likely containers without being too expensive
    if not el:
        return
    yield el
    # siblings
    sib = el.previous_sibling
    n = 0
    while sib and n < steps:
        if hasattr(sib, 'get_text'):
            yield sib
        sib = sib.previous_sibling
        n += 1
    sib = el.next_sibling
    n = 0
    while sib and n < steps:
        if hasattr(sib, 'get_text'):
            yield sib
        sib = sib.next_sibling
        n += 1
    # parent
    if el.parent:
        yield el.parent

def extract_duration(soup: BeautifulSoup) -> str | None:
    # 1) Obvious meta/info containers near the title
    meta_selectors = [
        'ul[class*="meta"]', 'ul[class*="info"]',
        'div[class*="meta"]', 'div[class*="info"]',
        'div[class*="brief"]', 'div[class*="top"]',
        '.program-meta', '.programme-meta', '.course-meta',
        '.inti_short', '.inti_top', '.program-top', '.single-program'
    ]
    for sel in meta_selectors:
        el = soup.select_one(sel)
        if el:
            d = _first_duration(el.get_text(' ', strip=True))
            if d:
                return d

    # 2) Look for a clock icon and read text in its container/siblings
    for icon in soup.select('i[class*="clock"], svg[class*="clock"], span[class*="clock"]'):
        for box in _nearby_elements(icon.parent if icon.parent else icon, steps=4):
            d = _first_duration(box.get_text(' ', strip=True))
            if d:
                return d

    # 3) Anchor by the money chip: "From RM 19,591" lives right next to duration
    for txt in soup.find_all(string=re.compile(r'(?i)\bfrom\s*rm\s*[\d,]+')):
        p = txt.parent
        for box in _nearby_elements(p, steps=4):
            d = _first_duration(box.get_text(' ', strip=True))
            if d:
                return d

    # 4) Conservative header scan (avoid matching module % etc.)
    main = soup.select_one('main') or soup.body
    head_text = (main.get_text(' ', strip=True) if main else soup.get_text(' ', strip=True))[:2000]
    return _first_duration(head_text)

def extract_course_info(url):
    """Fetch a course page and extract relevant information."""
    print(f"Fetching: {url}")

    # Headers to mimic a real browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # Find the inti_long div
        inti_long = soup.find('div', class_='inti_long')
        if not inti_long:
            print(f"  Warning: No inti_long div found on {url}")
            return None

        # Extract course information
        course_data = {
            'url': url,
            'sections': {}
        }

        dur = extract_duration(soup)
        if dur:
            course_data['sections']['Duration'] = dur

        # Define sections to extract
        sections_map = {
            'structure': 'Programme Structure',
            'fees': 'Fees',
            'entry_requirements': 'Entry Requirements',
            'campus_intake': 'Campus Intakes',
            'career-path': 'Career Opportunities',
            'highlights': 'Additional information'
        }

        # Extract each section
        for class_name, section_title in sections_map.items():
            section_div = inti_long.find('div', class_=class_name) or \
                          inti_long.find('div', class_=class_name.replace('-', '_'))

            if section_div:
                # Find the collapse div with actual content
                content_div = section_div.find('div', class_='collapse')
                if content_div:
                    if section_title == 'Fees':
                        # ✅ Only keep INTI International College Penang row
                        content = extract_penang_fee_markdown(content_div)
                    elif section_title == 'Campus Intakes':
                        # ✅ Only keep INTI International College Penang intakes
                        content = extract_penang_intakes_text(content_div)
                    else:
                        content = extract_text_from_element(content_div)
                        if section_title == 'Programme Structure':
                            content = cleanup_programme_structure_text(content)
                    if content:
                        course_data['sections'][section_title] = content

        return course_data

    except requests.RequestException as e:
        print(f"  Error fetching {url}: {e}")
        return None
    except Exception as e:
        print(f"  Error parsing {url}: {e}")
        return None


def get_program_type_and_name(url):
    """Extract program type and name from URL."""
    path = urlparse(url).path
    program_slug = path.rstrip('/').split('/')[-1]

    # Determine program type
    slug_l = program_slug.lower()
    if 'certificate' in slug_l:
        program_type = 'certificate'
    elif 'diploma' in slug_l:
        program_type = 'diploma'
    elif 'degree' in slug_l or 'bachelor' in slug_l:
        program_type = 'degree'
    elif 'foundation' in slug_l:
        program_type = 'foundation'
    elif 'master' in slug_l:
        program_type = 'master'
    else:
        program_type = 'degree'

    # Format program name (prettified)
    default_name = program_slug.replace('-', ' ').title()
    program_name = prettify_program_name(program_slug, default_name)

    return program_type, program_slug, program_name


def format_markdown(courses_by_type):
    """Format the extracted course data into markdown."""
    markdown_lines = []

    # Sort program types for consistent output
    type_order = ['foundation', 'certificate', 'diploma', 'degree', 'master', 'other']
    sorted_types = sorted(
        courses_by_type.keys(),
        key=lambda x: type_order.index(x) if x in type_order else len(type_order)
    )

    for program_type in sorted_types:
        # Add level 1 heading for program type
        markdown_lines.append(f"# {program_type.title()}")
        markdown_lines.append("")

        # Sort courses within each type
        courses = sorted(courses_by_type[program_type], key=lambda x: x['slug'])

        for course in courses:
            # Add level 2 heading for course name
            markdown_lines.append(f"## {heading_from_slug(course)}")
            markdown_lines.append("")

            # ✅ Add URL section
            markdown_lines.append("### URL")
            markdown_lines.append("")
            markdown_lines.append(course['url'])
            markdown_lines.append("")

            # Add each section in order
            section_order = [
                'Programme Structure',
                'Fees',
                'Entry Requirements',
                'Campus Intakes',
                'Duration',
                'Career Opportunities',
                'Additional information'
            ]

            for section_title in section_order:
                if section_title in course['sections']:
                    markdown_lines.append(f"### {section_title}")
                    markdown_lines.append("")
                    markdown_lines.append(course['sections'][section_title])
                    markdown_lines.append("")

            markdown_lines.append("---")
            markdown_lines.append("")

    return '\n'.join(markdown_lines)


def main():
    parser = argparse.ArgumentParser(
        description='Extract INTI course information and format into markdown'
    )
    parser.add_argument(
        'input_file',
        help='Input HTML file containing course URLs'
    )
    parser.add_argument(
        '--output',
        default='courses.md',
        help='Output markdown file (default: courses.md)'
    )
    parser.add_argument(
        '--delay',
        type=float,
        default=1.0,
        help='Delay between requests in seconds (default: 1.0)'
    )

    args = parser.parse_args()

    # Read input file
    print(f"Reading URLs from: {args.input_file}")
    try:
        with open(args.input_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except FileNotFoundError:
        print(f"Error: File '{args.input_file}' not found")
        return 1

    # Extract URLs
    urls = extract_urls_from_html(html_content)
    print(f"Found {len(urls)} unique URLs")
    if not urls:
        print("No URLs found in the input file")
        return 1

    # Extract course information
    courses_by_type = defaultdict(list)
    for i, url in enumerate(urls, 1):
        print(f"\nProcessing {i}/{len(urls)}")

        course_data = extract_course_info(url)

        if course_data and course_data['sections']:
            program_type, slug, name = get_program_type_and_name(url)

            courses_by_type[program_type].append({
                'slug': slug,
                'name': name,
                'url': course_data['url'],
                'sections': course_data['sections']
            })
        if i < len(urls):
            time.sleep(args.delay)

    # Format and write output
    print(f"\n\nGenerating markdown output...")
    markdown_content = format_markdown(courses_by_type)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    print(f"Output written to: {args.output}")
    print(f"Total courses processed: {sum(len(courses) for courses in courses_by_type.values())}")

    return 0


if __name__ == '__main__':
    exit(main())