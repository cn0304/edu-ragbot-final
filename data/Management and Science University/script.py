#!/usr/bin/env python3
"""
Usage:
    python3 "data/Management and Science University/script.py" \
      "data/Management and Science University/input.txt" \
      --output "data/Management and Science University/Courses.md"

"""

import argparse
import re
import time
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup, Tag, NavigableString
from collections import defaultdict


HEAD_PATTERNS = re.compile(
    r'^(?:year\s+(one|two|three|four)|semester\s+\d+|core(?:\s+modules)?|electives?)$',
    re.IGNORECASE
)

MSU_COLLEGE_PENANG_URL = "https://www.msucollege.edu.my/branches/msu-college-penang.php"
MSU_COLLEGE_PENANG_ENQUIRY_URL = "https://www2.msu.edu.my/msuc-enquiry/msuc-penang/"


def slugify(text: str) -> str:
    """Create a simple slug from a programme name."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "msu-college-penang-programme"

def _norm(s: str) -> str:
    s = re.sub(r'\s+', ' ', s or '').strip()
    # Title-case typical heads, keep “Semester 1/2”
    if re.match(r'^year\s+\w+$', s, flags=re.I):
        return s.title()
    if re.match(r'^semester\s+\d+$', s, flags=re.I):
        return s.title()
    if re.match(r'^core(\s+modules)?$', s, flags=re.I):
        return 'Core'
    if re.match(r'^electives?$', s, flags=re.I):
        return 'Electives'
    return s

def _is_heading_text(s: str) -> bool:
    return bool(HEAD_PATTERNS.match((s or '').strip()))

def _text(el: Tag) -> str:
    return ' '.join(list(el.stripped_strings)) if el else ''

def _collect_list_items(list_el: Tag) -> list:
    items = []
    for li in list_el.find_all('li', recursive=False):
        t = _text(li)
        if t:
            items.append(t)
    return items

def _split_bulletish_paragraph(p_text: str) -> list:
    """Fallback when the site used paragraphs with bullets in unicode or dashes."""
    t = p_text.replace('•', '\n•').replace('–', '\n-').replace('—', '\n-')
    lines = [re.sub(r'^[\-\•]\s*', '', l).strip() for l in t.splitlines()]
    return [l for l in lines if len(l) > 1]

def extract_programme_structure_markdown(soup: BeautifulSoup) -> str:
    # 1) Locate the container by a nearby heading
    candidates = []
    for h in soup.find_all(['h1','h2','h3','h4','h5','h6','a','strong','b']):
        ht = _text(h).lower()
        if any(k in ht for k in ['programme structure', 'program structure', 'courses offered', 'among the courses offered']):
            # Prefer the closest parent "content" block if present
            container = h.parent
            for _ in range(4):
                if container and container.name == 'div' and ('row' in (container.get('class') or []) or 'mb-3' in (container.get('class') or [])):
                    break
                container = container.parent if container else None
            candidates.append(container or h.parent)

    if not candidates:
        return ''

    container = candidates[0]
    groups = {}              # header -> [items]
    current = None
    default_header = 'Subjects/Modules'

    # 2) Walk forward collecting headings + list items
    for el in container.descendants:
        if not isinstance(el, Tag):
            continue

        # Heading tags or bold paragraphs acting as headings
        if el.name in ['h1','h2','h3','h4','h5','h6','strong','b']:
            tt = _norm(_text(el))
            if _is_heading_text(tt):
                current = tt
                groups.setdefault(current, [])
                continue

        # Typical UL/OL lists
        if el.name in ['ul','ol']:
            items = _collect_list_items(el)
            if items:
                if not current:
                    current = default_header
                groups.setdefault(current, [])
                groups[current].extend(items)

        # Sometimes “Year One” appears as a paragraph, followed by bullets styled with CSS
        if el.name == 'p':
            pt = _norm(_text(el))
            if _is_heading_text(pt):
                current = pt
                groups.setdefault(current, [])
                continue
            # Paragraph carries bullet-ish content
            if '•' in pt or re.search(r'\s-\s', pt):
                items = _split_bulletish_paragraph(pt)
                if items:
                    if not current:
                        current = default_header
                    groups.setdefault(current, [])
                    groups[current].extend(items)

    # 3) If we only got a flat list, keep it under default header
    if not groups:
        return ''

    # Deduplicate & clean
    for k, vs in list(groups.items()):
        seen = set()
        clean = []
        for v in vs:
            vv = v.strip()
            if vv and vv.lower() not in seen:
                seen.add(vv.lower())
                clean.append(vv)
        groups[k] = clean

    # 4) Order headings nicely
    def _order_key(h):
        hlow = h.lower()
        if hlow.startswith('year one'): return (1, h)
        if hlow.startswith('year two'): return (2, h)
        if hlow.startswith('year three'): return (3, h)
        if hlow.startswith('year four'): return (4, h)
        if hlow.startswith('semester '):
            try:
                n = int(re.findall(r'\d+', hlow)[0]); return (10+n, h)
            except Exception:
                return (15, h)
        if h == 'Core': return (5, h)
        if h == 'Electives': return (6, h)
        if h == default_header: return (20, h)
        return (19, h)

    parts = [ ]
    for head in sorted(groups.keys(), key=_order_key):
        items = groups[head]
        if not items:
            continue
        parts.append(f'#### {head}')
        for it in items:
            parts.append(f'- {it}')
        parts.append('')

    return '\n'.join(parts).strip()


def extract_urls_from_html(html_content):
    """Extract all course URLs from the MSU tab HTML."""
    soup = BeautifulSoup(html_content, "html.parser")
    urls = []

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if "msu.edu.my" in href and href.startswith("http"):
            if href not in urls:
                urls.append(href)
    return urls


def clean_text(text):
    """Clean and normalize text."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_duration_value(text):
    if not text:
        return ""

    t = clean_text(text)

    # Cut off when 'Credit Hours' or 'Recognition' appears
    lower = t.lower()
    for kw in ("credit hours", "recognition"):
        idx = lower.find(kw)
        if idx != -1:
            t = t[:idx]
            break

    return t.strip()

def strip_trailing_codes(full_name: str) -> str:
    name = full_name.strip()
    while True:
        m = re.search(r'\s*\(([^)]*)\)\s*$', name)
        if not m:
            break
        inner = m.group(1)
        # Heuristics: if it has digits, slashes, 'MQA' or looks like a code, drop it
        if re.search(r'\d', inner) or '/' in inner or 'mqa' in inner.lower():
            name = name[:m.start()].rstrip()
            continue
        # If the last () is just 'Hons' etc, keep it
        break
    return name or full_name.strip()

def extract_msu_college_penang_courses():
    print(f"Fetching MSU College Penang listing: {MSU_COLLEGE_PENANG_URL}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }

    try:
        res = requests.get(MSU_COLLEGE_PENANG_URL, headers=headers, timeout=30)
        res.raise_for_status()
    except Exception as e:
        print(f"  ✗ Error fetching MSU College Penang page: {e}")
        return {}

    soup = BeautifulSoup(res.text, "html.parser")

    # Find the "Programme Offered" heading
    heading = None
    for node in soup.find_all(string=re.compile(r"Programme Offered", re.I)):
        if isinstance(node, str):
            heading = node.parent
            break

    if not heading:
        print("  ✗ Could not find 'Programme Offered' section on MSU College Penang page.")
        return {}

    # Map text headings -> programme type used in markdown
    category_map = {
        "degree programme": "Degree",
        "diploma programme": "Diploma",
        "foundation programme": "Foundation",
        "certificate programme": "Certificate",
        "professional course": "Professional",
    }

    programmes_by_type = defaultdict(list)
    current_type = None

    # Standard stub text for sections
    stub = (
        "Detailed information (fees, duration, entry requirements and programme "
        "structure) is not listed separately for this programme on the MSU College "
        "Penang page.\n\n"
        f"For enquiries, please use the online form: {MSU_COLLEGE_PENANG_ENQUIRY_URL} "
    )

    for el in heading.next_siblings:
        if isinstance(el, NavigableString):
            continue
        if not isinstance(el, Tag):
            continue

        text = el.get_text(" ", strip=True)
        if not text:
            continue

        # Stop once we reach "Contact Us"
        if re.search(r"contact us", text, re.I):
            break

        lower_text = text.lower()

        # Category heading (Degree Programme / Diploma Programme / etc.)
        if lower_text in category_map:
            current_type = category_map[lower_text]
            continue

        # Programme list items under current category
        if current_type:
            for li in el.find_all("li"):
                full_name = li.get_text(" ", strip=True)
                if not full_name:
                    continue

                # Remove only trailing MQA / code / date brackets, keep (Hons) etc.
                name = strip_trailing_codes(full_name)

                s = slugify(name)

                sections = {
                    "Programme Structure": stub,
                    "Fee": stub,
                    "Duration": stub,
                    "Entry Requirements": stub,
                }

                programmes_by_type[current_type].append(
                    {
                        "slug": s,
                        "title": name,
                        "url": MSU_COLLEGE_PENANG_URL,
                        "sections": sections,
                    }
                )

    if not programmes_by_type:
        print("  ✗ No programmes found in MSU College Penang listing.")
        return {}

    return dict(programmes_by_type)

def extract_course_info(url):
    """Fetch and extract key course information from MSU course page."""
    print(f"Fetching: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }

    try:
        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()
    except Exception as e:
        print(f"  ✗ Error fetching: {e}")
        return None

    soup = BeautifulSoup(res.text, "html.parser")
    course_data = {"url": url, "sections": {}}

    # Find all major content blocks (existing behaviour, kept as fallback)
    content_blocks = soup.find_all("div", class_=re.compile(r"mb-3"))
    rows = soup.find_all("div", class_="row")

    def extract_by_heading(title_keywords):
        """
        Prefer reading a section by its heading anywhere on the page.

        Handles both:
        - simple layouts: <h4>Entry Requirements</h4><p>...</p>
        - card layouts:   <div class="card-header"><h4>Entry Requirements</h4></div>
                          <div class="card-body">...</div>
        """
        HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

        def collect_from_container(node: Tag, heading_text_lower: str) -> str:
            """Get text from a container, trimming the heading line."""
            if not isinstance(node, Tag):
                return ""
            raw = node.get_text(separator="\n", strip=True)
            if not raw:
                return ""
            lines = [l.strip() for l in raw.splitlines() if l.strip()]
            if lines and heading_text_lower in lines[0].lower():
                # drop the heading line itself
                lines = lines[1:]
            return clean_text("\n".join(lines))

        for tag in soup.find_all(HEADING_TAGS):
            heading_text = tag.get_text(strip=True)
            heading_text_lower = heading_text.lower()
            if not any(kw in heading_text_lower for kw in title_keywords):
                continue

            # --- 1) Simple case: siblings directly after the heading ---
            parts = []
            for sib in tag.next_siblings:
                if isinstance(sib, Tag) and sib.name in HEADING_TAGS:
                    break
                if isinstance(sib, Tag):
                    txt = sib.get_text(separator="\n", strip=True)
                else:
                    txt = str(sib).strip()
                if txt:
                    parts.append(txt)
            if parts:
                return clean_text("\n".join(parts))

            # --- 2) Card layout: climb a few div parents and use that as container ---
            parent = tag
            for _ in range(4):  # don't climb all the way to <body>
                if not parent or not isinstance(parent, Tag):
                    break
                if parent.name == "div":
                    headings_inside = parent.find_all(HEADING_TAGS)
                    # good candidate: this div only has this one heading
                    if len(headings_inside) == 1:
                        text = collect_from_container(parent, heading_text_lower)
                        if text:
                            return text
                parent = parent.parent

        return ""

    def extract_block(title_keywords):
        """
        Extract text for a section identified by its heading.

        1) Try global heading-based extraction (robust for new layouts).
        2) If nothing found, fall back to the older mb-3/row-based search.
        """
        # 1) Global search by heading
        text = extract_by_heading(title_keywords)
        if text:
            return text

        # 2) Fallback: old behaviour limited to mb-3 / row blocks
        for div in content_blocks + rows:
            # broaden to any heading tag, not just <h5>
            heading = div.find(["h1", "h2", "h3", "h4", "h5", "h6", "a"])
            if heading:
                heading_text = heading.get_text(strip=True).lower()
                if any(kw in heading_text for kw in title_keywords):
                    text = div.get_text(separator="\n", strip=True)
                    return clean_text(text)
        return ""

    # Extract key sections
    about = extract_block(["about"])
    entry = extract_block(["entry"])
    duration_raw = extract_block(["duration"])
    duration = clean_duration_value(duration_raw)
    fee = extract_block(["fee"])
    # Prefer structured parsing
    programme_md = extract_programme_structure_markdown(soup)
    programme = programme_md or extract_block(["courses offered", "programme structure"])

    career = extract_block(["career"])

    if programme:
        course_data["sections"]["Programme Structure"] = programme
        print("  ✓ Programme Structure")
    if fee:
        course_data["sections"]["Fee"] = fee
        print("  ✓ Fee")
    if duration:
        course_data["sections"]["Duration"] = duration
        print("  ✓ Duration")
    if entry:
        course_data["sections"]["Entry Requirements"] = entry
        print("  ✓ Entry Requirements")
    if about:
        course_data["sections"]["About"] = about
        print("  ✓ About")
    if career:
        course_data["sections"]["Career Prospects"] = career
        print("  ✓ Career Prospects")

    if not course_data["sections"]:
        print("  ✗ No sections extracted")

    return course_data


def get_program_type_and_name(url):
    """Infer program type and slug from URL."""
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.php$", "", slug, flags=re.IGNORECASE)
    lower = slug.lower()

    if "diploma" in lower:
        ptype = "Diploma"
    elif "bachelor" in lower or "degree" in lower:
        ptype = "Degree"
    elif "master" in lower:
        ptype = "Master"
    elif "phd" in lower or "doctor" in lower:
        ptype = "PhD"
    else:
        ptype = "Other"

    return ptype, slug


def format_markdown(courses_by_type):
    """Format the extracted data into Markdown."""
    lines = []
    # Include extra types used by MSU College Penang
    order = ["Foundation", "Certificate", "Professional", "Diploma", "Degree", "Master", "PhD", "Other"]

    for ptype in order:
        if ptype not in courses_by_type:
            continue

        lines.append(f"# {ptype}")
        lines.append("")

        for course in sorted(courses_by_type[ptype], key=lambda x: x["slug"]):
            slug = course["slug"]
            title = course.get("title") or slug

            # Use the slug in the H2 so ingest can recognise it as a course
            lines.append(f"## {slug}")
            lines.append("")

            # URL section
            lines.append("### URL")
            lines.append("")
            lines.append(course["url"])
            lines.append("")

            for section_title in [
                "Programme Structure",
                "Fee",
                "Duration",
                "Entry Requirements",
                "Career Prospects",
                "About",
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
    parser = argparse.ArgumentParser(description="Extract MSU course info to markdown")
    parser.add_argument("input_file", help="Input HTML file containing course URLs")
    parser.add_argument("--output", default="msu_courses.md", help="Output markdown file")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests")
    args = parser.parse_args()

    # Read file (can be real HTML, or just something containing the Penang URL)
    try:
        with open(args.input_file, "r", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        print(f"Error: {args.input_file} not found")
        return 1

    # 🔸 SPECIAL CASE: MSU College Penang – no individual course URLs
    if MSU_COLLEGE_PENANG_URL in html:
        print("Detected MSU College Penang – using programme-list mode.\n")
        courses_by_type = extract_msu_college_penang_courses()
        if not courses_by_type:
            print("No programmes could be extracted from MSU College Penang page.")
            return 1

        markdown = format_markdown(courses_by_type)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"✓ Output written to {args.output}")
        return 0

    # Normal MSU (www.msu.edu.my) scraping from programme URLs
    urls = extract_urls_from_html(html)
    print(f"Found {len(urls)} course URLs.\n")

    if not urls:
        return 1

    courses_by_type = defaultdict(list)
    success, fail = 0, 0

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}]")
        info = extract_course_info(url)
        if info and info["sections"]:
            ptype, slug = get_program_type_and_name(url)
            courses_by_type[ptype].append({
                "slug": slug,
                "url": info["url"],   # ✅ Add URL
                "sections": info["sections"]
            })
            success += 1
        else:
            fail += 1
        if i < len(urls):
            time.sleep(args.delay)

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
