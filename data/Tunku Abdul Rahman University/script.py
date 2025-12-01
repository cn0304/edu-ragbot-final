#!/usr/bin/env python3
"""
Usage:
    python3 "data/Tunku Abdul Rahman University/script.py" \
      "data/Tunku Abdul Rahman University/input.txt" \
      --output "data/Tunku Abdul Rahman University/Courses.md"
"""


import argparse
import re
import time
import requests
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from collections import defaultdict


def _table_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    out = []
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join(["---"] * len(header)) + " |")
    for r in body:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def extract_urls_from_file(filepath):
    """Extract all course URLs from the input file."""
    urls = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if line and not line.startswith("#"):
                    if line.startswith("http"):
                        urls.append(line)
    except Exception as e:
        print(f"Error reading file: {e}")
    return urls


def clean_text(text):
    """Clean and normalize text."""
    if not text:
        return ""
    # Remove excessive whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove inline styles and other noise
    text = re.sub(r'style="[^"]*"', '', text)
    return text.strip()

def extract_entry_requirements(soup: BeautifulSoup, base_url: str, session=None) -> str | None:
    """
    Priority: TABLE -> LINK (incl. buttons/onclick/data-*) -> 'here' inside a sentence
    mentioning Minimum Entry Requirements -> IMAGE -> meaningful TEXT.
    Never return just the words 'Minimum Entry Requirement(s):'.
    """
    patt = re.compile(r'\b(minimum\s+entry\s+requirements?|entry\s+requirements?)\b', re.I)
    heading_only_re = re.compile(
        r'^\s*(minimum\s+entry\s+requirements?|entry\s+requirements?)\s*:?\s*$', re.I
    )

    def abs_url(href: str) -> str:
        return urljoin(base_url, href)

    # candidate containers around the heading text
    def candidate_roots():
        roots = []
        for node in soup.find_all(string=patt):
            cur, steps = node.parent, 0
            while cur and steps < 8 and cur.name not in ('section', 'article', 'div', 'main', 'body'):
                cur, steps = cur.parent, steps + 1
            roots.append(cur or node.parent)
        # de-dup; fall back to whole doc
        return list(dict.fromkeys([r for r in roots if r])) or [soup]

    def _table_to_md(tbl):
        rows = []
        for tr in tbl.find_all('tr'):
            cells = [c.get_text(" ", strip=True).replace("\xa0", " ") for c in tr.find_all(['th', 'td'])]
            if cells:
                rows.append(cells)
        return _table_to_markdown(rows) if rows else None

    def anchor_to_link(a):
        href = a.get('href')
        if not href:
            # onclick="window.open('...')" / "location.href='...'"
            onclick = a.get('onclick', '') or ''
            m = re.search(r"['\"](https?://[^'\"\s]+)['\"]", onclick)
            if m:
                href = m.group(1)
        if href:
            label = a.get_text(' ', strip=True) or 'Minimum Entry Requirement'
            return f"[{label}]({abs_url(href)})"
        return None

    def elem_to_link(el):
        # <button/div/span> with data-* or onclick
        for k in ('data-href', 'data-url', 'data-link'):
            if el.get(k):
                label = el.get_text(' ', strip=True) or 'Minimum Entry Requirement'
                return f"[{label}]({abs_url(el[k])})"
        onclick = el.get('onclick', '') or ''
        m = re.search(r"['\"](https?://[^'\"\s]+)['\"]", onclick)
        if m:
            label = el.get_text(' ', strip=True) or 'Minimum Entry Requirement'
            return f"[{label}]({abs_url(m.group(1))})"
        return None

    # If a sentence mentions "Minimum Entry Requirements" and contains an <a> (e.g., "click here"),
    # return the full sentence with the Markdown link injected on that anchor text.
    def sentence_with_here_link(a) -> str | None:
        href = a.get('href')
        if not href:
            return None
        parent = a.find_parent(['p', 'li', 'div']) or a.parent
        if not parent:
            return None
        text = parent.get_text(' ', strip=True)
        if not text or not patt.search(text):
            return None
        label = (a.get_text(' ', strip=True) or 'here').strip()
        md = f"[{label}]({abs_url(href)})"
        # Replace the first occurrence of the label (case-insensitive) with the markdown link
        try:
            replaced = re.sub(re.escape(label), md, text, count=1, flags=re.I)
        except re.error:
            replaced = text.replace(label, md, 1)
        return replaced

    roots = candidate_roots()

    # ---- 1) TABLE ----
    for root in roots + [soup]:
        t = root.find('table')
        if t:
            md = _table_to_md(t)
            if md:
                return md

    # ---- 2) LINK (anchor/button near heading) ----
    # (keeps your two cases that already have clickable links)
    for root in roots + [soup]:
        # a) anchors whose label/href implies entry requirements or is a PDF/image
        for a in root.find_all('a'):
            label = a.get_text(' ', strip=True)
            href = a.get('href', '')
            if patt.search(label) or re.search(r'(entry|requirement)', href or '', re.I) or re.search(r'\.(pdf|png|jpe?g)$', href or '', re.I):
                link = anchor_to_link(a)
                if link:
                    return link
            # b) Surrounding sentence mentions entry requirements; anchor text is "here"
            sent = sentence_with_here_link(a)
            if sent:
                return sent

        # c) buttons/divs/spans that behave like links
        for el in root.find_all(['button', 'div', 'span']):
            label = el.get_text(' ', strip=True)
            if patt.search(label):
                link = elem_to_link(el)
                if link:
                    return link

    # ---- 3) IMAGE (closest to the heading) ----
    for root in roots:
        img = root.find('img')
        if not img:
            anchor_node = root.find(string=patt)
            if anchor_node:
                nxt = anchor_node.parent
                hops = 0
                while nxt and hops < 30:
                    nxt = nxt.find_next()
                    hops += 1
                    if getattr(nxt, "name", None) == "img":
                        img = nxt
                        break

        if img and img.get("src"):
            # Link to the programme page itself, not the long Google image URL
            return f"[Minimum Entry Requirements (image)]({base_url})"

    # ---- 4) TEXT (but not just the heading) ----
    text_bits = []
    for root in roots + [soup]:
        for blk in root.find_all(['ul', 'ol', 'p', 'li']):
            t = blk.get_text(' ', strip=True)
            if not t or heading_only_re.match(t):
                continue
            low = t.lower()
            if any(s in low for s in ('apply now', 'expand', 'collapse')):
                continue
            if len(t) >= 25 or re.search(r'\b(SPM|UEC|O\s*Level|credits?|grades?|CGPA|Mathematics)\b', t, re.I):
                text_bits.append(t)
        if text_bits:
            break

    if text_bits:
        return "\n".join(text_bits[:8])

    return None


def _strip_leading_bullet(s: str) -> str:
    # remove any leading list markers like "-", "•", "·"
    return re.sub(r'^\s*[-–—•·]\s*', '', s or '').strip()


def format_programme_structure(lines: list[str]) -> str:
    stop_re = re.compile(r'^(exemption|progression|career\s+prospects)\b', re.I)
    main_hdr_re = re.compile(r'^(programme\s+outline|programme\s+structure)\s*:?\s*$', re.I)

    sub_hdr_re = re.compile(
        r'^(?:'
        r'common\s+courses|'
        r'accountancy\s+specialisation\s+courses|'
        r'finance\s+specialisation\s+courses|'
        r'business\s+specialisation\s+courses|'
        r'compulsory\s+courses|'
        r'compulsory\s+subjects|'
        r'language[^:\n]*mata[^:\n]*pengajian[^:\n]*umum[^:\n]*(?:\(mpu\))?[^:\n]*co-?curricular[^:\n]*courses?'
        r')\s*:?\s*(.*)$',
        re.I
    )

    def is_flat_section(name: str) -> bool:
        s = (name or "").lower()
        return (
            'compulsory' in s
            or 'mpu' in s
            or ('mata pelajaran pengajian umum' in s and 'co-curricular' in s)
            or (s.startswith('language') and 'co-curricular' in s)
        )

    # --- Pre-merge broken parenthetical chunks across lines
    # e.g. "Industrial Training (2" + "0 weeks)" -> "Industrial Training (20 weeks)"
    merged_lines: list[str] = []
    i = 0
    while i < len(lines):
        cur = clean_text(lines[i])
        if not cur:
            i += 1
            continue

        # Detect an open '(' not yet closed on this line
        open_paren = cur.rfind('(')
        close_paren = cur.rfind(')')
        if open_paren != -1 and (close_paren == -1 or close_paren < open_paren) and i + 1 < len(lines):
            nxt = clean_text(lines[i + 1] or '')
            # Join if the next line likely closes the parentheses or contains 'weeks'
            if nxt and (')' in nxt or re.search(r'\bweeks?\b', nxt, re.I)):
                combined = (cur + ' ' + nxt).strip()
                # Fix spaced digits inside the parentheses: "2 0" -> "20"
                combined = re.sub(r'\(([^)]*?)\)', lambda m: '(' + re.sub(r'(?<=\d)\s+(?=\d)', '', m.group(1)) + ')', combined)
                merged_lines.append(combined)
                i += 2
                continue

        merged_lines.append(cur)
        i += 1

    def normalize_weeks(text: str) -> str:
        # "(2 0 weeks)" -> "(20 weeks)" ; also tolerate singular 'week'
        def _fix(m):
            num = re.sub(r'\s+', '', m.group(1))
            return f"({num} weeks)"
        return re.sub(r'\(\s*([\d\s]+)\s*weeks?\s*\)', _fix, text, flags=re.I)

    out: list[str] = []
    in_outline = False
    active_section: str | None = None
    seen: set[str] = set()

    general_buf: list[str] = []  # bullet lines
    flat_buf: list[str] = []     # for Compulsory / MPU-Language

    # Track 'Industrial Training (X weeks)' to override 0 with non-zero
    training_idx = None
    training_weeks = None  # None or int

    def add_bullet(text: str):
        nonlocal training_idx, training_weeks
        t = re.sub(r'^\s*[-–—•·]\s*', '', text or '').strip()
        if not t:
            return
        t = normalize_weeks(t)

        # Handle Industrial Training specially
        mt = re.match(r'(?i)industrial\s+training\s*\(\s*(\d+)\s*weeks?\s*\)', t)
        if mt:
            weeks = int(mt.group(1))
            if training_idx is None:
                # first time we see it
                general_buf.append(t)
                training_idx = len(general_buf) - 1
                training_weeks = weeks
                seen.add(t.lower())
                return
            # we've seen one before: prefer non-zero
            if (training_weeks or 0) == 0 and weeks > 0:
                general_buf[training_idx] = t  # upgrade 0 -> non-zero
                training_weeks = weeks
            # if we already have non-zero, ignore duplicates
            return

        key = t.lower()
        if key not in seen:
            general_buf.append(t)
            seen.add(key)

    def add_flat(text: str):
        t = (text or '')
        t = normalize_weeks(t)
        t = re.sub(r'^\s*[-–—•·,/]+\s*', '', t).strip()
        t = t.strip().lstrip(',/').rstrip('.,/')
        if not t:
            return
        parts = [p.strip().rstrip('.,/') for p in re.split(r',\s*', t) if p.strip()]
        for p in parts:
            p = re.sub(r'(?<=\d)\s+(?=\d)', '', p)  # fix spaced digits inside items
            k = p.lower()
            if k and k not in seen:
                flat_buf.append(p)
                seen.add(k)

    def flush_buffers():
        nonlocal general_buf, flat_buf
        if active_section and is_flat_section(active_section):
            if flat_buf:
                line = ", ".join(flat_buf)
                if not line.endswith('.'):
                    line += '.'
                out.append(line)
                out.append("")
            flat_buf = []
        else:
            if general_buf:
                for it in general_buf:
                    out.append(f"- {it}")
                out.append("")
            general_buf = []

    for raw in merged_lines:
        txt = clean_text(raw)
        if not txt:
            continue

        if stop_re.match(txt):
            break

        if main_hdr_re.match(txt):
            if not out or out[-1] != "**Programme Outline**":
                out.append("**Programme Outline**")
                out.append("")
            in_outline = True
            continue

        if not in_outline:
            continue

        m = sub_hdr_re.match(txt)
        if m:
            flush_buffers()
            hdr_line = re.sub(r'\s*:\s*$', '', re.sub(r'\s+', ' ', txt)).strip()
            out.append(f"**{hdr_line}**")
            out.append("")
            active_section = hdr_line

            trailing = (m.group(1) or "").strip()
            if trailing:
                (add_flat if is_flat_section(active_section) else add_bullet)(trailing)
            continue

        (add_flat if (active_section and is_flat_section(active_section)) else add_bullet)(txt)

    flush_buffers()
    while out and out[-1] == "":
        out.pop()

    return "\n".join(out).strip()


def extract_entry_requirement_link(soup: BeautifulSoup, base_url: str) -> str | None:
    """
    Find an anchor whose text mentions 'Minimum Entry Requirement' and return Markdown link.
    """
    # 1) Any obvious anchor
    for a in soup.find_all('a', href=True):
        label = a.get_text(' ', strip=True)
        if re.search(r'\bminimum\s+entry\s+requirements?\b', label, re.I):
            href = a['href']
            href = urljoin(base_url, href)
            return f"[Minimum Entry Requirement]({href})"

    # 2) Heading near an anchor
    for hdr in soup.find_all(re.compile(r'^h[1-6]$')):
        if re.search(r'\bminimum\s+entry\s+requirements?\b', hdr.get_text(' ', strip=True), re.I):
            # descendant link
            a = hdr.find('a', href=True)
            if a:
                return f"[Minimum Entry Requirement]({urljoin(base_url, a['href'])})"
            # next sibling link
            sib = hdr.find_next_sibling()
            while sib and sib.name not in ['a', 'p', 'div', 'ul', 'ol', 'section']:
                sib = sib.find_next_sibling()
            if sib:
                a = sib.find('a', href=True) if hasattr(sib, 'find') else None
                if a:
                    return f"[Minimum Entry Requirement]({urljoin(base_url, a['href'])})"

    return None


def normalize_fee_text(text):
    """Normalize fee text by fixing broken numbers like 'RM 37,2 00' or '2 0'."""
    # Join numbers split around commas: "37 , 2 00" -> "37,200"
    text = re.sub(r'(\d+)\s*,\s*(\d+)', r'\1,\2', text)
    # Remove spaces between digits anywhere: "2 0" -> "20", "6 00" -> "600"
    text = re.sub(r'(?<=\d)\s+(?=\d)', '', text)
    # Squeeze extra spaces
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def extract_section_by_heading(text_lines, heading_keywords, stop_keywords, include_heading_lines=False):
    result = []
    capturing = False

    for line in text_lines:
        line_clean = clean_text(line)
        if not line_clean:
            continue
        line_lower = line_clean.lower()

        # Start capturing when we see any heading keyword
        if any(kw in line_lower for kw in heading_keywords):
            capturing = True
            if include_heading_lines:
                # keep the heading line (strip trailing colon)
                result.append(re.sub(r'\s*:\s*$', '', line_clean))
            else:
                # legacy behavior
                if len(line_clean) > 50 or any(word in line_lower for word in ["year", "intake", "duration", "rm "]):
                    result.append(line_clean)
            continue

        # Stop when we hit any stop keyword
        if capturing and any(kw in line_lower for kw in stop_keywords):
            break

        # Capture content (skip obvious nav)
        if capturing and len(line_clean) > 2 and not any(
                s in line_lower for s in ["click here", "apply now", "looking for more", "expand", "collapse"]):
            result.append(line_clean)

    return result


def extract_fee_info(soup, lines):
    """
    Extract fee information more carefully by looking at HTML structure.
    This handles cases where numbers are split across multiple spans.
    """
    fee_content = []

    # Strategy 1: Find all <p> or <div> tags that contain "Estimated Total Fees"
    fee_elements = soup.find_all(string=re.compile(r'Estimated Total Fees', re.IGNORECASE))

    for fee_elem in fee_elements:
        # Get the parent paragraph/div
        parent = fee_elem.find_parent(['p', 'div', 'span'])
        if parent:
            # Get the full parent container
            container = parent.find_parent(['p', 'div'])
            if container:
                # Extract text from this container and next siblings
                current = container
                collected_text = []

                for _ in range(5):  # Check up to 5 sibling elements
                    if current:
                        text = current.get_text(separator=" ", strip=True)
                        text = normalize_fee_text(text)

                        # Stop at scholarship mentions
                        if any(stop in text.lower() for stop in
                               ["merit scholarship", "other scholarship", "financial aid", "for ptptn"]):
                            break

                        if text and len(text) > 5:
                            collected_text.append(text)

                        current = current.find_next_sibling()
                    else:
                        break

                if collected_text:
                    return collected_text

    # Strategy 2: Fall back to line-based extraction
    for i, line in enumerate(lines):
        if "estimated total fees" in line.lower():
            # Collect this line and next few lines
            for j in range(i, min(i + 8, len(lines))):
                current_line = lines[j]

                # Stop at scholarship mentions
                if any(stop in current_line.lower() for stop in
                       ["merit scholarship", "other scholarship", "financial aid", "for ptptn", "to find out more"]):
                    break

                # Include lines with fee information
                if any(indicator in current_line.lower() for indicator in
                       ["rm", "estimated", "fees", "may vary", "note:", "sst", "malaysian", "international"]):
                    # Normalize the fee text
                    normalized = normalize_fee_text(current_line)
                    # Skip "click here" and similar
                    if "click here" not in normalized.lower() and "for more information" not in normalized.lower():
                        fee_content.append(normalized)
            break

    # Clean up collected fees
    cleaned_fees = []
    for fee_line in fee_content:
        # Skip empty or too short
        if len(fee_line.strip()) < 5:
            continue
        # Skip lines that are just "Fees" or headers
        if fee_line.strip().lower() in ["fees", "fees:", "estimated fees"]:
            continue
        cleaned_fees.append(fee_line.strip())

    return cleaned_fees[:6]  # Limit to first 6 relevant lines

def extract_programme_outline_html(soup: BeautifulSoup) -> list[str]:
    heading_re = re.compile(r'\bprogramme\s+(outline|structure)\b', re.I)

    heading_node = None
    for node in soup.find_all(string=heading_re):
        txt = (str(node) or "").strip()
        if txt:
            heading_node = node
            break

    if not heading_node:
        return []

    lines: list[str] = []
    # Normalised heading text (e.g. "Programme Outline")
    lines.append(clean_text(str(heading_node)))

    parent = heading_node.parent
    if not parent:
        return lines

    def is_stop_text(text: str) -> bool:
        low = text.lower()
        return (
            re.match(r'^(exemption|progression|career\s+prospects)\b', low)
            or low.startswith("campus:")
            or low.startswith("intake:")
            or low.startswith("duration:")
            or "estimated total fees" in low
            or "fees & financial aid" in low
            or "minimum entry requirement" in low
        )

    # Walk siblings after the heading container
    for sib in parent.next_siblings:
        name = getattr(sib, "name", None)

        # Skip pure whitespace strings / non-tag noise
        if not name:
            continue

        text = sib.get_text(" ", strip=True)
        text = clean_text(text)
        if not text:
            continue

        if is_stop_text(text):
            break

        # Bullet lists – collect each <li> separately
        if name in ("ul", "ol"):
            for li in sib.find_all("li", recursive=False):
                item = clean_text(li.get_text(" ", strip=True))
                if item:
                    lines.append(item)
        else:
            # Headers like "Elective 1 (Choose 1):", MPU line, etc.
            lines.append(text)

    return lines


def extract_course_info(url):
    """Fetch and extract key course information from TARC course page."""
    print(f"Fetching: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()
    except Exception as e:
        print(f"  ✗ Error fetching: {e}")
        return None

    soup = BeautifulSoup(res.text, "html.parser")
    course_data = {"url": url, "sections": {}}

    # Get full text for easier parsing
    full_text = soup.get_text(separator="\n", strip=True)
    lines = [clean_text(line) for line in full_text.split("\n") if clean_text(line)]

    # ============ Extract Programme Outline/Structure ============
    # Special hard-coded case for:
    # https://focs.tarc.edu.my/programmes/bachelor-degree/bachelor-in-data-science-honours-rds
    if url.rstrip("/").endswith("bachelor-in-data-science-honours-rds"):
        course_data["sections"]["Programme Structure"] = """**Programme Outline**

- Problem Solving and Programming
- Database Management
- Introduction to Computer Security
- Probability and Statistics
- System Analysis and Design
- Discrete Mathematics
- Fundamentals of Computer Networks
- Computer Organisation and Architecture
- Object-Oriented Programming
- Operating Systems
- Statistics for Data Science
- Software Engineering
- Data Science
- Data Visualisation
- Data Engineering
- Machine Learning
- Data Structures and Algorithms
- Artificial Intelligence
- Project I
- Project II
- Data Warehouse Technology
- Industrial Training (5 months)

**Elective 1 (Choose 1):**

- Algebra and Calculus
- Advanced Discrete Mathematics

**Elective 2 (Choose 1):**

- Natural Language Processing
- Web-Based Integrated Systems

**Elective 3 (Choose 1):**

- Internet of Things
- Graphics Programming

**Elective 4 (Choose 1):**

- Image Processing
- Blockchain Application Development

**Elective 5 (Choose 1):**

- Advanced Database Management
- Mobile Application Development

**Elective 6 (Choose 1):**

- Cloud Computing
- Distributed Systems and Parallel Computing

**Free Electives (Choose 1):**

- Japanese Language I
- French Language I
- Korean Language I

**Language, Mata Pelajaran Pengajian Umum (MPU) and Co-curricular Courses:**

English for Tertiary Studies, Falsafah dan Isu Semasa, Academic English, Penghayatan Etika dan Peradaban, English for Career Preparation, Entrepreneurship/ Bahasa Kebangsaan A, Integrity and Anti Corruption, Co-curricular
"""
        print("  ✓ Programme Structure (hardcoded for RDS)")
    else:
        programme_lines = extract_section_by_heading(
            lines,
            heading_keywords=[
                "programme outline", "programme structure",
                "common courses",
                "accountancy specialisation courses",
                "finance specialisation courses",
                "business specialisation courses",
                "specialisation courses",
                "compulsory courses",
                "compulsory subjects"
            ],
            stop_keywords=[
                # don't stop on "compulsory ..." anymore
                "exemption", "progression", "career prospects",
                "campus:", "minimum entry requirement", "intake:", "duration:",
                "estimated total fees", "fees & financial aid"
            ],
            include_heading_lines=True
        )

        if programme_lines:
            course_data["sections"]["Programme Structure"] = format_programme_structure(programme_lines)
            print("  ✓ Programme Structure")
        else:
            print("  ✗ Programme Structure not found")

    # ============ Extract Fees ============
    fee_content = extract_fee_info(soup, lines)

    if fee_content:
        course_data["sections"]["Fee"] = "\n".join(fee_content)
        print("  ✓ Fee")
    else:
        course_data["sections"]["Fee"] = "You can find fee information online"
        print("  ✗ Fee (not found - using fallback)")

    # ============ Extract Duration ============
    duration_content = []
    for i, line in enumerate(lines):
        ll = line.lower()
        if ll.strip() == "duration:" or ll.startswith("duration:"):
            # Collect duration on same line or immediate next meaningful line
            for j in range(i, min(i + 4, len(lines))):
                cur = lines[j].strip()
                if j == i and ":" in cur:
                    val = cur.split(":", 1)[1].strip()
                    if val:
                        duration_content.append(val)
                        break
                elif j > i and cur and not any(kw in cur.lower() for kw in ["intake", "fees", "estimated", "campus"]):
                    duration_content.append(cur)
                    break
            break

    if duration_content:
        dur_text = " ".join(duration_content).strip()

        # If it's just a number like "2" -> "2 Years"
        if re.fullmatch(r'\d+(?:\.\d+)?', dur_text):
            n = float(dur_text)
            unit = "Year" if abs(n - 1.0) < 1e-9 else "Years"
            dur_text = f"{dur_text} {unit}"

        course_data["sections"]["Duration"] = dur_text
        print("  ✓ Duration")
    else:
        print("  ✗ Duration not found")

    # ============ Extract Intakes ============
    intake_content = []

    for i, line in enumerate(lines):
        if line.lower().strip() == "intake:" or "intake:" in line.lower():
            # Capture everything until we hit duration or fees
            for j in range(i, min(i + 15, len(lines))):
                current_line = lines[j]

                # Stop conditions
                if j > i and any(kw in current_line.lower() for kw in
                                 ["duration:", "estimated total fees", "campus:", "minimum entry"]):
                    break

                # Include relevant lines
                if j == i:
                    if ":" in current_line:
                        after_colon = current_line.split(":", 1)[1].strip()
                        if after_colon and len(after_colon) > 5:
                            intake_content.append(current_line)
                    else:
                        intake_content.append(current_line)
                else:
                    if any(indicator in current_line.lower() for indicator in
                           ["year ", "intake", "january", "february", "march", "april", "may", "june", "july", "august",
                            "september", "october", "november", "december", "kuala lumpur", " kl", "penang", " pg"]):
                        intake_content.append(current_line)
                    elif current_line and len(current_line) < 50 and not any(
                            skip in current_line.lower() for skip in ["click", "http", "expand", "collapse"]):
                        intake_content.append(current_line)
            break

    if not intake_content:
        for i, line in enumerate(lines):
            if "intakes:" in line.lower():
                for j in range(i + 1, min(i + 5, len(lines))):
                    if "duration:" in lines[j].lower() or "estimated" in lines[j].lower():
                        break
                    if lines[j] and not lines[j].startswith("http"):
                        intake_content.append(lines[j])

    if intake_content:
        course_data["sections"]["Intake"] = "\n".join(intake_content)
        print("  ✓ Intake")
    else:
        print("  ✗ Intake not found")

    # ============ Extract Entry Requirements ============
    er = extract_entry_requirements(soup, url)
    if er:
        course_data["sections"]["Entry Requirements"] = er
        print("  ✓ Entry Requirements")
    else:
        course_data["sections"][
            "Entry Requirements"
        ] = "Please refer to the Minimum Entry Requirements on the course page."
        print("  ✗ Entry Requirements (not found - fallback)")

    # ============ Extract Campus (optional but helpful) ============
    campus_lines = extract_section_by_heading(
        lines,
        heading_keywords=["campus:"],
        stop_keywords=["intake:", "duration:", "estimated"]
    )

    if campus_lines:
        # Limit to first 5 lines to avoid capturing too much
        course_data["sections"]["Campus"] = "\n".join(campus_lines[:5])
        print("  ✓ Campus")

    if not course_data["sections"]:
        print("  ✗ No sections extracted")

    return course_data


def get_program_type_and_name(url):
    """Infer program type and name from URL, and prefix level if missing."""
    path = urlparse(url).path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    slug = parts[-1] if parts else "unknown"

    path_lower = path.lower()

    # --- detect type from path ---
    if "foundation" in path_lower:
        ptype = "Foundation"
    elif "/diploma/" in path_lower and "advanced" not in path_lower:
        ptype = "Diploma"
    elif (
        "bachelor" in path_lower
        or "bachelors-degree" in path_lower
        or "bachelor-degree" in path_lower
    ):
        ptype = "Degree"
    elif "master" in path_lower:
        ptype = "Master"
    elif "phd" in path_lower or "doctor" in path_lower:
        ptype = "PhD"
    else:
        ptype = "Other"

    # --- normalise slug so it carries the level when missing ---
    slug_lower = slug.lower()

    if ptype == "Foundation" and "foundation" not in slug_lower:
        slug = f"foundation-{slug}"
    elif ptype == "Diploma" and "diploma" not in slug_lower:
        slug = f"diploma-{slug}"
    elif ptype == "Degree" and not any(
        key in slug_lower for key in ("bachelor", "degree", "hons", "honours")
    ):
        # TARC style: add "bachelor-" when degree word not present
        slug = f"bachelor-{slug}"

    return ptype, slug


def format_markdown(courses_by_type):
    """Format the extracted data into Markdown."""
    lines = []
    order = ["Foundation", "Diploma", "Degree", "Master", "PhD", "Other"]

    for ptype in order:
        if ptype not in courses_by_type:
            continue

        lines.append(f"# {ptype}")
        lines.append("")

        for course in sorted(courses_by_type[ptype], key=lambda x: x["slug"]):
            lines.append(f"## {course['slug']}")
            lines.append("")
            lines.append(f"### URL")
            lines.append("")
            lines.append(course['url'])
            lines.append("")

            # Fixed order of sections
            for section_title in [
                "Programme Structure",
                "Fee",
                "Duration",
                "Intake",
                "Entry Requirements",
                "Campus",
            ]:
                if section_title in course["sections"]:
                    lines.append(f"### {section_title}")
                    lines.append("")
                    lines.append(course["sections"][section_title])
                    lines.append("")

            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Extract TARC course info to markdown")
    parser.add_argument("input_file", help="Input file containing course URLs")
    parser.add_argument("--output", default="tarc_courses.md", help="Output markdown file")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between requests (seconds)")
    args = parser.parse_args()

    # Read URLs from file
    urls = extract_urls_from_file(args.input_file)
    print(f"Found {len(urls)} course URLs.\n")

    if not urls:
        print("No URLs found in input file")
        return 1

    courses_by_type = defaultdict(list)
    success, fail = 0, 0

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}]")
        info = extract_course_info(url)

        if info and info["sections"]:
            ptype, slug = get_program_type_and_name(url)
            courses_by_type[ptype].append({"slug": slug, "url": url, "sections": info["sections"]})
            success += 1
        else:
            fail += 1

        if i < len(urls):
            time.sleep(args.delay)
        print()

    print(f"\nCompleted: {success} success, {fail} failed.\n")

    if success > 0:
        markdown = format_markdown(courses_by_type)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"✓ Output written to {args.output}")
    else:
        print("No successful extractions.")

    return 0


if __name__ == "__main__":
    exit(main())