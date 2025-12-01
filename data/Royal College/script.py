#!/usr/bin/env python3
"""
Usage:
    python3 "data/Royal College/script.py" \
      "data/Royal College/input.txt" \
      --output "data/Royal College/Courses.md"
"""

import argparse
import re
import time
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from collections import defaultdict


def extract_urls_from_text(file_content):
    """Extract all course URLs from the text file."""
    urls = []
    for line in file_content.splitlines():
        line = line.strip()
        if line.startswith("http") and "rumc.edu.my" in line:
            urls.append(line)
    return urls


def clean_text(text):
    """Clean and normalize extracted text."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def extract_semester_outline(soup):
    import re
    from bs4.element import Tag, NavigableString

    # ---- Locate the "Course Outline" section (robust) ----
    trigger_rx = re.compile(r'\b(Course Outline|Programme Course|Programme Course Outline)\b', re.IGNORECASE)
    trigger = soup.find(string=trigger_rx)
    container = None
    if trigger:
        # climb to a parent that actually holds multiple Semester/Year blocks
        node = trigger.parent if isinstance(trigger, NavigableString) else trigger
        cur = node
        for _ in range(8):
            if not cur: break
            # Heuristic: the right container should have >= 2 Semester/Year headings OR a table with "Semester"
            has_labels = cur.find(string=re.compile(r'(?i)\b(Semester|Year)\b'))
            tables = cur.find_all('table')
            table_has_sem = any(t.find(string=re.compile(r'(?i)semester')) for t in tables)
            if cur is not node and (len(cur.find_all(string=re.compile(r'(?i)\b(Semester|Year)\b'))) >= 2 or table_has_sem):
                container = cur
                break
            cur = cur.parent
    # fallback: whole page (will still be guarded below)
    if not container:
        container = soup

    def _norm_semester(txt: str) -> str:
        t = re.sub(r'\s+', ' ', txt).strip()
        # SEMESTER I / II / III / 1 / 2 / 3
        m = re.match(r'(?i)semester\s+([ivx]+|\d+)', t)
        if m:
            tok = m.group(1)
            num = tok if tok.isdigit() else tok.upper()
            return f"Semester {num}"
        # YEAR 1 - 3, YEAR 3 – 5, etc.
        m = re.match(r'(?i)year\s+(.+)', t)
        if m:
            return f"Year {m.group(1).strip()}"
        return t.title()

    parts = []

    def _dedupe_list(items):
        import re
        kill = re.compile(r'(?i)^(semester\s+[ivx\d]+|short\s*semester|year\s+.+)$')
        seen, out = set(), []
        for it in items:
            k = re.sub(r'\s+', ' ', it).strip()
            if not k or kill.match(k) or k in seen:
                continue
            seen.add(k)
            out.append(k)
        return out

    # ---- 1) Table layout (Pre-Medical) ----
    for tbl in container.find_all('table'):
        # include only tables that look like semester tables
        head_txt = tbl.get_text(" ", strip=True)
        if not re.search(r'(?i)semester', head_txt):
            continue

        # Build columns map by visual column index
        sem_cols = {}
        # try to read header cells first
        header = tbl.find('thead') or tbl.find('tr')
        headers = []
        if header and header.name != 'thead':
            headers = [th.get_text(" ", strip=True) for th in header.find_all(['th','td'])]
        elif header and header.name == 'thead':
            headers = [th.get_text(" ", strip=True) for th in header.find_all('th')]

        header_tr = None
        if header:
            header_tr = header.find('tr') if header.name == 'thead' else header

        for idx, h in enumerate(headers):
            if re.search(r'(?i)semester', h):
                sem_cols[idx] = _norm_semester(h)

        # If headers didn’t label semesters, assume first two columns are Semester 1/2
        if not sem_cols:
            sem_cols = {0: "Semester 1", 1: "Semester 2"}

        items = {name: [] for name in sem_cols.values()}

        # iterate data rows
        for tr in tbl.find_all('tr'):
            if header_tr is not None and tr is header_tr:
                continue  # don't treat the header row as data
            cells = tr.find_all(['td','th'])
            if not cells:
                continue

            # one wide cell (spanning both) → append to all semesters
            if len(cells) == 1 or (cells[0].has_attr('colspan') and int(cells[0]['colspan']) >= 2):
                text = cells[0].get_text(" ", strip=True)
                text = re.sub(r'\s+', ' ', text).strip()
                if text:
                    for k in items:
                        items[k].append(text)
                continue

            # normal two+ columns
            for idx, cell in enumerate(cells):
                if idx in sem_cols:
                    text = cell.get_text(" ", strip=True)
                    text = re.sub(r'\s+', ' ', text).strip()
                    if text:
                        items[sem_cols[idx]].append(text)

        # write out if we actually captured something
        if any(items.values()):
            # keep column order by numeric suffix if possible
            def key_order(k):
                m = re.search(r'(\d+)$', k)
                return int(m.group(1)) if m else 99

            for label in sorted(items.keys(), key=key_order):
                cleaned = _dedupe_list(items[label])
                if not cleaned:
                    continue
                parts.append(f"**{label}**")
                parts.append("")
                parts += [f"- {it}" for it in cleaned]
                parts.append("")

    if parts:
        return "\n".join(parts).strip()

    # ---- 2) Card/column layout (Semester or Year headers with <ul>) ----
    # Find all header-like nodes inside the container
    header_nodes = []
    for node in container.find_all(True):
        txt = node.get_text(" ", strip=True)
        if not txt:
            continue
        if re.match(r'(?i)^\s*(semester\s+(?:[ivx]+|\d+)|year\s+.+)\s*$', txt):
            header_nodes.append(node)

    if not header_nodes:
        return None

    seen = set()
    for hdr in header_nodes:
        label = _norm_semester(hdr.get_text(" ", strip=True))
        if label in seen:
            continue
        seen.add(label)

        # find the nearest UL that belongs to the same column/box
        # prefer a UL inside the same parent block
        search_block = hdr
        for _ in range(4):
            if search_block.find_next_sibling('ul'):
                break
            if search_block.parent:
                search_block = search_block.parent
            else:
                break

        ul = search_block.find_next('ul') or hdr.find_next('ul')

        # stop if the next header appears before that UL
        nxt_hdr = hdr.find_next(string=re.compile(r'(?i)^\s*(semester|year)\b'))
        if ul and nxt_hdr:
            try:
                if hasattr(ul, 'sourceline') and hasattr(nxt_hdr, 'sourceline'):
                    if ul.sourceline and nxt_hdr.sourceline and ul.sourceline > nxt_hdr.sourceline:
                        ul = None
            except Exception:
                pass

        items = []
        if ul:
            for li in ul.find_all('li'):
                t = li.get_text(" ", strip=True)
                t = re.sub(r'\s+', ' ', t).strip()
                if t:
                    items.append(t)

        # guard: don’t accidentally crawl into other sections (Entry/Fees/etc.)
        if not items:
            sib = hdr
            hops = 0
            blocker = re.compile(r'(?i)\b(Entry|Requirement|Fee|Duration|Admission|Scholarship)\b')
            while hops < 6:
                sib = sib.find_next_sibling()
                if not sib:
                    break
                if sib.get_text(" ", strip=True) and blocker.search(sib.get_text(" ", strip=True)):
                    break
                if sib.find(string=re.compile(r'(?i)^\s*(semester|year)\b')):
                    break
                for li in sib.find_all('li'):
                    t = li.get_text(" ", strip=True)
                    t = re.sub(r'\s+', ' ', t).strip()
                    if t:
                        items.append(t)
                if items:
                    break
                hops += 1

        if not items:
            continue

        parts.append(f"**{label}**")
        parts.append("")
        parts += [f"- {it}" for it in items]
        parts.append("")

    return ("\n".join(parts).strip() or None)

def extract_year_semester_table(soup):
    import re
    parts = []

    def norm_sem_label(t):
        t = re.sub(r'\s+', ' ', t).strip()
        if re.search(r'(?i)short\s*semester', t):
            return "Short Semester"
        m = re.search(r'(?i)semester\s*([ivx]+|\d+)', t)
        if m:
            tok = m.group(1)
            return f"Semester {tok if tok.isdigit() else tok.upper()}"
        return None

    def norm_year_label(t):
        m = re.search(r'(?i)^year\s+(\d+)', re.sub(r'\s+', ' ', t).strip())
        return f"Year {m.group(1)}" if m else None

    def dedupe(lines):
        seen, out = set(), []
        for x in lines:
            k = re.sub(r'\s+', ' ', x).strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def flush_year(year_label, buckets, out):
        if not year_label:
            return
        out.append(f"**{year_label}**")
        out.append("")
        def order_key(k):
            if k == "Short Semester": return 99
            m = re.search(r'(\d+)$', k)
            return int(m.group(1)) if m else 50
        for sem in sorted(buckets.keys(), key=order_key):
            items = dedupe(buckets[sem])
            if not items:
                continue
            out.append(f"**{sem}**")
            out.append("")
            out += [f"- {it}" for it in items]
            out.append("")

    found_any = False
    for tbl in soup.find_all('table'):
        txt_all = tbl.get_text(" ", strip=True)
        if not re.search(r'(?i)\bsemester\b', txt_all):
            continue

        current_year = None
        sem_map = {}      # col_idx -> "Semester X"/"Short Semester"
        buckets = {}      # "Semester X" -> [items]

        # We walk rows in order, resetting on each Year row, and mapping the next row with Semester labels.
        for tr in tbl.find_all('tr'):
            cells = tr.find_all(['th', 'td'])
            if not cells:
                continue
            cell_texts = [c.get_text(" ", strip=True) for c in cells]

            # 1) YEAR row (usually a single cell spanning columns)
            year_here = None
            for t in cell_texts:
                yl = norm_year_label(t)
                if yl:
                    year_here = yl
                    break
            if year_here and (len(cells) == 1 or (cells[0].has_attr('colspan') and int(cells[0]['colspan']) >= 3)):
                # flush previous year block first
                if current_year and buckets:
                    flush_year(current_year, buckets, parts)
                current_year = year_here
                sem_map = {}
                buckets = {}
                found_any = True
                continue

            # 2) SEMESTER header row (labels like "Semester 1 | Semester 2 | Short Semester")
            if any(norm_sem_label(t) for t in cell_texts):
                sem_map = {}
                buckets = {}
                for i, t in enumerate(cell_texts):
                    lab = norm_sem_label(t)
                    if lab:
                        sem_map[i] = lab
                        buckets.setdefault(lab, [])
                continue

            # 3) DATA row: add text to the proper semester bucket(s)
            if sem_map:
                for i, c in enumerate(cells):
                    if i not in sem_map:
                        continue
                    raw = c.get_text("\n", strip=True)
                    lines = [re.sub(r'\s+', ' ', s).strip() for s in raw.split('\n')]
                    for line in lines:
                        if line:
                            buckets[sem_map[i]].append(line)

        # flush last year in this table
        if current_year and buckets:
            flush_year(current_year, buckets, parts)

    md = "\n".join(parts).strip()
    return md if md else None

def extract_undergrad_medicine_cards(soup):
    """
    Parse the three 'Undergraduate Programme Course' cards.
    Skips roll-up <li> items that repeat the entire list as one bullet.
    """
    import re

    def norm(txt: str) -> str:
        return re.sub(r'\s+', ' ', (txt or '')).strip()

    MPU_RX = re.compile(r'(?i)\bMPU\b|Important\s+Note\s+on\s+MPU')

    parts = []
    seen_labels = set()

    # Only headings that are exactly "YEAR X - Y"
    year_nodes = soup.find_all(
        string=lambda s: isinstance(s, str) and re.match(r'(?i)^\s*year\s*\d+\s*[-–]\s*\d+\s*$', s.strip())
    )

    for ynode in year_nodes:
        # climb to a container that actually holds the list(s)
        cur = ynode.parent
        container = None
        for _ in range(6):
            if not cur:
                break
            if cur.find('ul'):
                container = cur
                break
            cur = cur.parent

        if not container:
            continue

        year_label = norm(ynode).title()  # "Year 1 - 3", "Year 3 - 5"

        # Region (IRELAND / MALAYSIA): try bold tags first, then plain text
        region = None
        region_el = container.find(lambda t: getattr(t, "name", "") in ("strong", "b")
                                             and re.search(r'(?i)\b(ireland|malaysia)\b', t.get_text()))
        if region_el:
            region = norm(region_el.get_text()).upper()
        else:
            region_text = container.find(string=re.compile(r'(?i)\b(ireland|malaysia)\b'))
            if region_text:
                region = norm(region_text).upper()

        # Optional sublabel
        sublabel = None
        for s in container.find_all(string=True):
            txt = norm(s)
            if re.match(r'(?i)^(systems:|each\s+system\s+consists\s+of:)\s*$', txt):
                sublabel = txt.rstrip(':').title()
                break

        label = year_label
        if region:
            label += f" ({region})"
        if sublabel:
            label += f" – {sublabel}"

        if label in seen_labels:
            continue
        seen_labels.add(label)

        bullets = []
        for ul in container.find_all('ul'):
            # collect raw li texts from this UL
            raw = []
            for li in ul.find_all('li'):
                t = norm(li.get_text(" ", strip=True))
                if not t or MPU_RX.search(t):
                    continue
                raw.append(t)

            if not raw:
                continue

            # Identify and drop "roll-up" items:
            # if an li contains >= 3 other li texts inside it, it's a roll-up.
            keep = []
            for i, t in enumerate(raw):
                if len(t) > 80:  # quick guard: only long candidates can be roll-ups
                    contain_cnt = 0
                    for j, other in enumerate(raw):
                        if i == j:
                            continue
                        # consider only reasonably short "unit" items
                        if 2 <= len(other) <= 60 and other in t:
                            contain_cnt += 1
                    if contain_cnt >= 3:
                        # skip this roll-up bullet
                        continue
                keep.append(t)

            bullets.extend(keep)

        # De-dupe across all ULs while keeping order
        seen_b = set()
        clean = []
        for b in bullets:
            if b in seen_b:
                continue
            seen_b.add(b)
            clean.append(b)

        if not clean:
            continue

        parts.append(f"**{label}**")
        parts.append("")
        parts += [f"- {b}" for b in clean]
        parts.append("")

    md = "\n".join(parts).strip()
    return md or None

def extract_course_info(url):
    """Fetch and extract course information from a RUMC page."""
    print(f"Fetching: {url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/119.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()
    except Exception as e:
        print(f"  ✗ Error fetching: {e}")
        return None

    soup = BeautifulSoup(res.text, "html.parser")
    course_data = {"url": url, "sections": {}}

    # ====== Extract Duration & Intake ======
    info_boxes = soup.find_all("div", class_="info-box-wrapper")
    for box in info_boxes:
        title = box.find("h4")
        content = box.find("div", class_="info-box-inner")
        if title and content:
            key = title.get_text(strip=True).lower()
            value = clean_text(content.get_text(separator="\n", strip=True))
            if "duration" in key:
                course_data["sections"]["Duration"] = value
                print("  ✓ Duration")
            elif "intake" in key:
                course_data["sections"]["Intake"] = value
                print("  ✓ Intake")
            elif "fee" in key or "fees" in key:
                course_data["sections"]["Fee"] = value
                print("  ✓ Fee")

    # ====== Extract main content sections ======
    def extract_by_id(section_ids, title):
        for sid in section_ids:
            section = soup.find("div", id=sid)
            if section:
                text = section.get_text(separator="\n", strip=True)
                if text:
                    course_data["sections"][title] = clean_text(text)
                    print(f"  ✓ {title}")
                    return

    extract_by_id(
        ["course-structure", "programme-structure", "programme-delivery", "course-outline", "curriculum-structure"],
        "Programme Structure"
    )
    extract_by_id(["fees"], "Fee")
    extract_by_id(["entry","entry-requirements"], "Entry Requirements")

    sem_md = extract_semester_outline(soup)
    if sem_md:
        course_data["sections"]["Programme Structure"] = sem_md
        print("  ✓ Programme Structure (Course Outline)")
    # ====== Masters special-case override (non-destructive) ======
    lower_url = url.lower()
    if "medical-informatics" in lower_url:
        mi_md = extract_year_semester_table(soup)
        if mi_md:
            course_data["sections"]["Programme Structure"] = mi_md
            print("  ✓ Programme Structure (Medical Informatics year/semester table)")

    if "undergraduate-medicine" in lower_url:
        ug_md = extract_undergrad_medicine_cards(soup)
        if ug_md:
            course_data["sections"]["Programme Structure"] = ug_md
            print("  ✓ Programme Structure (Undergraduate Medicine cards)")

    # A) This specific MSc page: always use Teaching & Learning list instead of tables/years
    if "master-of-science-in-occupational-therapy" in lower_url:
        course_data["sections"]["Programme Structure"] = (
            "**Teaching and Learning Methods**\n\n"
            "- Lectures\n- Research\n- Thesis"
        )
        print("  ✓ Programme Structure (Teaching & Learning override for MSc OT)")

    # B) Generic fallback for other Masters pages that have no clear structure extracted
    elif ("master" in lower_url or "postgraduate" in lower_url) and \
         "Programme Structure" not in course_data["sections"]:
        course_data["sections"]["Programme Structure"] = (
            "**Teaching and Learning Methods**\n\n"
            "- Lectures\n- Research\n- Thesis"
        )
        print("  ✓ Programme Structure (Teaching & Learning fallback)")

    if not course_data["sections"]:
        print("  ✗ No sections extracted")

    return course_data


def get_program_type_and_name(url):
    """Infer program type (Foundation, Degree, Master) from URL or folder."""
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.php$", "", slug, flags=re.IGNORECASE)
    lower = slug.lower()

    if "foundation" in lower:
        ptype = "Foundation"
    elif "diploma" in lower:
        ptype = "Diploma"
    elif "undergraduate" in lower or "bachelor" in lower or "degree" or "informatics" in lower:
        ptype = "Degree"
    elif "master" in lower:
        ptype = "Master"
    else:
        ptype = "Other"

    return ptype, slug


def format_markdown(courses_by_type):
    """Format extracted course data into Markdown output."""
    lines = []
    order = ["Foundation", "Diploma", "Degree", "Master", "Other"]

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
            for section_title in [
                "Programme Structure",
                "Fee",
                "Duration",
                "Intake",
                "Entry Requirements",
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
    parser = argparse.ArgumentParser(description="Extract RUMC course info to markdown")
    parser.add_argument("input_file", help="Input text file containing RUMC URLs")
    parser.add_argument("--output", default="Courses.md", help="Output markdown file")
    parser.add_argument("--delay", type=float, default=1.2, help="Delay between requests")
    args = parser.parse_args()

    # Read URL file
    try:
        with open(args.input_file, "r", encoding="utf-8") as f:
            file_content = f.read()
    except FileNotFoundError:
        print(f"Error: {args.input_file} not found")
        return 1

    urls = extract_urls_from_text(file_content)
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
            courses_by_type[ptype].append({"slug": slug, "url": url, "sections": info["sections"]})
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
