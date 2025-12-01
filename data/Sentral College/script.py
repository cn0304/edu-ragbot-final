#!/usr/bin/env python3
"""
Usage:
    python3 "data/Sentral College/script.py" \
      "data/Sentral College/input.txt" \
      --output "data/Sentral College/Courses.md"
"""

import argparse
import re
import time
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from collections import defaultdict


def clean_text(text):
    """Normalize whitespace and strip unwanted spaces."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def element_to_bullets(container):
    """
    Convert a BeautifulSoup container's lists/paragraphs into '- ' markdown bullets.
    - Prefers <li> items if present (from any nested <ul>/<ol>).
    - Falls back to splitting lines / bullet symbols if lists aren't used.
    """
    if not container:
        return ""

    items = []

    # 1) Prefer real list items
    for li in container.find_all("li"):
        txt = clean_text(li.get_text(" ", strip=True))
        if txt:
            items.append(txt)

    # 2) Fallback: break plain text into lines/bullets
    if not items:
        raw = container.get_text("\n", strip=True)
        # Split on newlines or bullet dots (•)
        candidates = [clean_text(x) for x in re.split(r"\n|•", raw) if clean_text(x)]
        items = candidates

    # 3) De-dupe while keeping order
    seen = set()
    unique = []
    for it in items:
        key = it.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(f"- {it}")

    return "\n".join(unique) if unique else ""

def programme_structure_with_mpu(pane):
    """
    Extract Programme Structure with an **MPU Subjects** sub-heading if present.
    Falls back to '' so caller can use element_to_bullets(pane).
    """
    # 1) Find the MPU header node (various tags possible)
    mpu_header = pane.find(string=re.compile(r"^\s*MPU\s+Subjects\s*$", re.I))
    if not mpu_header:
        return ""  # nothing special; let caller fall back

    # 2) MPU items = <li> in the first <ul>/<ol> after the header
    mpu_ul = mpu_header.find_parent().find_next(["ul", "ol"])
    mpu_items = []
    if mpu_ul:
        for li in mpu_ul.find_all("li"):
            txt = clean_text(li.get_text(" ", strip=True))
            if txt:
                mpu_items.append(txt)

    # 3) Main items = all other <li> in the pane that are NOT in the MPU list
    mpu_li_set = set(mpu_ul.find_all("li")) if mpu_ul else set()
    main_items = []
    for li in pane.find_all("li"):
        if mpu_ul and li in mpu_li_set:
            continue
        txt = clean_text(li.get_text(" ", strip=True))
        if txt:
            main_items.append(txt)

    # 4) De-dupe while keeping order
    def dedupe_keep_order(items):
        seen, out = set(), []
        for it in items:
            k = it.lower()
            if k not in seen:
                seen.add(k)
                out.append(it)
        return out

    main_items = dedupe_keep_order(main_items)
    mpu_items = dedupe_keep_order(mpu_items)

    # 5) Build markdown with bold sub-heading
    lines = []
    lines += [f"- {it}" for it in main_items]
    if mpu_items:
        if lines:
            lines.append("")  # blank line before sub-section
        lines.append("**MPU Subjects**")
        lines += [f"- {it}" for it in mpu_items]

    return "\n".join(lines).strip()

def programme_structure_with_mpu_and_elective(pane):
    """
    Extract Programme Structure with **Elective** and **MPU Subjects** sub-headings (if present).
    Returns '' if nothing special is found so caller can fall back safely.
    """
    # find headers
    elective_hdr = pane.find(string=re.compile(r"^\s*Elective\s*:?\s*$", re.I))
    mpu_hdr = pane.find(string=re.compile(r"^\s*MPU\s+Subjects\s*:?\s*$", re.I))

    # collect UL/OL right after each header
    def next_list_after(header):
        if not header:
            return None
        parent = header.find_parent()
        return parent.find_next(["ul", "ol"]) if parent else None

    elective_ul = next_list_after(elective_hdr)
    mpu_ul = next_list_after(mpu_hdr)

    # gather items under those sections
    def li_texts(ul):
        out = []
        if not ul:
            return out
        for li in ul.find_all("li"):
            txt = clean_text(li.get_text(" ", strip=True))
            if txt:
                out.append(txt)
        return out

    elective_items = li_texts(elective_ul)
    mpu_items = li_texts(mpu_ul)

    # if neither section exists, do nothing (let caller fall back)
    if not elective_items and not mpu_items:
        return ""

    # build a set of LI nodes that belong to subsections (to exclude from main list)
    subsection_lis = set()
    if elective_ul:
        subsection_lis.update(elective_ul.find_all("li"))
    if mpu_ul:
        subsection_lis.update(mpu_ul.find_all("li"))

    # main programme items = all other <li> in the pane, excluding subsection lis
    main_items = []
    for li in pane.find_all("li"):
        if li in subsection_lis:
            continue
        txt = clean_text(li.get_text(" ", strip=True))
        if txt:
            main_items.append(txt)

    # de-dupe keep order
    def dedupe_keep_order(items):
        seen, out = set(), []
        for it in items:
            k = it.lower()
            if k not in seen:
                seen.add(k)
                out.append(it)
        return out

    main_items = dedupe_keep_order(main_items)
    elective_items = dedupe_keep_order(elective_items)
    mpu_items = dedupe_keep_order(mpu_items)

    # assemble markdown
    lines = []
    lines += [f"- {it}" for it in main_items]
    if elective_items:
        if lines:
            lines.append("")
        lines.append("**Elective**")
        lines += [f"- {it}" for it in elective_items]
    if mpu_items:
        if lines:
            lines.append("")
        lines.append("**MPU Subjects**")
        lines += [f"- {it}" for it in mpu_items]

    return "\n".join(lines).strip()


def programme_structure_with_year_groups(pane):
    """
    Handle accordion-style sections like 'Year 1', 'Year 2', 'Year 3', and 'Elective Subjects'.
    Returns '' if no such headers are found, so the caller can fall back safely.
    """
    # Find headers in document order
    hdr_regex = re.compile(r"^\s*(Year\s*\d+|Elective\s+Subjects)\s*$", re.I)
    headers = list(pane.find_all(string=hdr_regex))
    if not headers:
        return ""

    def next_list_after(header):
        if not header:
            return None
        parent = header.find_parent()
        return parent.find_next(["ul", "ol"]) if parent else None

    # Collect items section-by-section
    sections = []
    for hdr in headers:
        title = clean_text(str(hdr))
        ul = next_list_after(hdr)
        if not ul:
            continue
        items = []
        for li in ul.find_all("li"):
            txt = clean_text(li.get_text(" ", strip=True))
            if txt:
                items.append(txt)

        # de-dupe keep order per section
        seen, uniq = set(), []
        for it in items:
            k = it.lower()
            if k not in seen:
                seen.add(k)
                uniq.append(it)

        if uniq:
            sections.append((title, uniq))

    if not sections:
        return ""

    # Assemble Markdown with bold sub-headings
    lines = []
    for idx, (title, items) in enumerate(sections):
        if idx > 0:
            lines.append("")  # blank line between sections
        lines.append(f"**{title}**")
        lines += [f"- {it}" for it in items]

    return "\n".join(lines).strip()

def programme_structure_with_years_and_clusters(pane):
    """
    Handles:
      • **Year 1/2/3**
      • **Elective Subjects**
      • **Elective Subject Cluster** with bold sub-fields (Marketing, Enterprise System, Human Resource)
    Returns '' if nothing matched so caller can fall back safely.
    """
    # Main headers we care about (document order)
    main_hdr_re = re.compile(
        r"^\s*(Year\s*\d+|Elective\s+Subjects?|Elective\s+Papers?|Practical\s+Sessions?|Elective\s+Subject\s+Cluster)\s*$",
        re.I
    )

    headers = list(pane.find_all(string=main_hdr_re))
    if not headers:
        return ""

    def next_list_after(node):
        if not node:
            return None
        parent = node.find_parent()
        return parent.find_next(["ul", "ol"]) if parent else None

    def li_texts(ul):
        out = []
        if not ul:
            return out
        for li in ul.find_all("li"):
            txt = clean_text(li.get_text(" ", strip=True))
            if txt:
                out.append(txt)
        return out

    def dedupe_keep_order(items):
        seen, out = set(), []
        for it in items:
            k = it.lower()
            if k not in seen:
                seen.add(k)
                out.append(it)
        return out

    lines = []
    # Known cluster sub-sections we should bold
    cluster_titles = re.compile(r"^\s*(Marketing|Enterprise\s*System|Human\s*Resource|Finance)\s*$", re.I)

    for hdr in headers:
        title = clean_text(str(hdr))

        # Simple Year/Elective lists (one list after the header)
        if re.match(r"^\s*(Year\s*\d+|Elective\s+(?:Subjects?|Papers?)|Practical\s+Sessions?)\s*$", title, re.I):
            ul = next_list_after(hdr)
            items = dedupe_keep_order(li_texts(ul))
            if items:
                if lines:
                    lines.append("")
                lines.append(f"**{title}**")
                lines += [f"- {it}" for it in items]
            continue

        # Elective Subject Cluster: multiple sub-headers + lists until next main header
        if re.match(r"^\s*Elective\s+Subject\s+Cluster\s*$", title, re.I):
            # Announce the cluster title once
            had_any_cluster = False
            if lines:
                lines.append("")
            lines.append("**Elective Subject Cluster**")

            # Walk forward until the next main header
            stop_at = set(h for h in headers if h is not hdr)
            # Use next_elements to sweep forward
            for node in hdr.find_parent().next_elements:
                # If we hit another main header string, stop the sweep
                if isinstance(node, str) and any(node is s for s in stop_at):
                    break

                # Pick up sub-section titles like Marketing / Enterprise System / Human Resource
                if isinstance(node, (str,)) and cluster_titles.match(clean_text(str(node)) or ""):
                    sub_title = clean_text(str(node))
                    # Find the list after this sub-title
                    parent = None
                    try:
                        parent = node.parent  # type: ignore[attr-defined]
                    except Exception:
                        parent = None
                    ul = parent.find_next(["ul", "ol"]) if parent else None
                    items = dedupe_keep_order(li_texts(ul))
                    if items:
                        had_any_cluster = True
                        lines.append(f"**{sub_title}**")
                        lines += [f"- {it}" for it in items]
                        lines.append("")  # spacer between sub-sections

            # Trim trailing blank line inside the cluster
            while lines and lines[-1] == "":
                lines.pop()

    return "\n".join(lines).strip()


def extract_intakes_and_duration(soup):
    """
    Robustly pull 'Intakes' (list items) and 'Duration' (short text),
    regardless of the exact tags/classes used.
    """
    out = {
        "Intakes": "result not found in online",
        "Duration": "result not found in online",
    }

    # Prefer a sidebar-like container if available; otherwise search the whole page.
    def _sidebar_like(tag):
        if not hasattr(tag, "get"):
            return False
        classes = " ".join(tag.get("class", []))
        return (
            tag.name in ("div", "aside", "section")
            and re.search(r"(intakes|duration)", classes, re.I)
        )

    scope = soup.find(_sidebar_like) or soup

    # ---------------- INTAKES ----------------
    intakes_label = scope.find(string=re.compile(r"\bINTAKES\b", re.I))
    if intakes_label:
        parent = intakes_label.parent
        ul = parent.find_next("ul") if parent else None
        if ul:
            items = [
                clean_text(li.get_text(" ", strip=True))
                for li in ul.find_all("li")
                if clean_text(li.get_text(" ", strip=True))
            ]
            if items:
                out["Intakes"] = ", ".join(items)

    # ---------------- DURATION ----------------
    duration_label = scope.find(string=re.compile(r"\bDURATION\b", re.I))
    if duration_label:
        label_tag = duration_label.parent  # usually <strong>
        label_re = r"^\s*DURATION\s*[:\-]?\s*"

        # climb out of inline tags (<strong>, <span>, <b>, <em>, <i>) to the block (<p>/<div>)
        block = label_tag
        while block is not None and block.name in ("strong", "b", "em", "span", "i"):
            block = block.parent
        if block is None:
            block = label_tag

        text = ""

        # 1) Try value inside the SAME block:
        #    <p><strong>DURATION</strong><br>2 Years 4 Months</p>
        if hasattr(block, "get_text"):
            raw = clean_text(block.get_text(" ", strip=True))
            raw_wo_label = re.sub(label_re, "", raw, flags=re.I).strip()
            if raw_wo_label and raw_wo_label.lower() != "duration":
                text = raw_wo_label

        # 2) If still empty, try the NEXT SIBLING block:
        #    <p><strong>DURATION</strong></p><p>2 Years 4 Months</p>
        parent_block = block
        if not text and parent_block is not None:
            sib = parent_block.find_next_sibling()
            while sib is not None:
                if getattr(sib, "name", None) in ("p", "div", "span", "li", "strong"):
                    cand_raw = clean_text(sib.get_text(" ", strip=True))
                    if cand_raw:
                        cand = re.sub(label_re, "", cand_raw, flags=re.I).strip()
                        if cand and cand.lower() != "duration":
                            text = cand
                            break
                sib = sib.find_next_sibling()

        # 3) Last fallback: next suitable tag anywhere after block
        if not text and block is not None:
            nxt = block.find_next(
                lambda t: getattr(t, "name", None) in ("p", "div", "span", "li", "strong")
                and clean_text(t.get_text(" ", strip=True)),
            )
            if nxt:
                raw = clean_text(nxt.get_text(" ", strip=True))
                raw_wo_label = re.sub(label_re, "", raw, flags=re.I).strip()
                cand = raw_wo_label or raw
                if cand and cand.lower() != "duration":
                    text = cand

        if text:
            # 4) Safety cut if we accidentally captured a big blob
            split_tokens = [
                r"Entry Requirement",
                r"Programme Structure",
                r"Career Opportunities",
                r"Career Prospects",
                r"Learning Outcome",
                r"Learning Outcomes",
                r"\bFAQ\b",
            ]
            split_re = re.compile("|".join(split_tokens), flags=re.I)
            text = split_re.split(text)[0].strip()

            if text and text.lower() != "duration":
                out["Duration"] = text

    return out

def extract_urls_from_file(input_file):
    """Extract valid URLs from the txt file."""
    urls = []
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("http"):
                urls.append(line)
    return urls


def extract_course_info(url):
    """Fetch and extract course details from Sentral College programme page."""
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

    # Extract Intakes and Duration (robust – works even if label is inside <strong>)
    id_result = extract_intakes_and_duration(soup)

    course_data["sections"]["Intakes"] = id_result["Intakes"]
    course_data["sections"]["Duration"] = id_result["Duration"]

    if id_result["Intakes"] != "result not found in online":
        print("  ✓ Intakes")
    else:
        print("  ✗ Intakes not found")

    if id_result["Duration"] != "result not found in online":
        print("  ✓ Duration")
    else:
        print("  ✗ Duration not found")

    # Tabs (Entry Requirement, Programme Structure, etc.)
    tabs = soup.find("div", class_=re.compile(r"programme__tabs"))
    if not tabs:
        print("  ✗ Tabs not found")
        for section in ["Programme Structure", "Entry Requirements", "Fee"]:
            course_data["sections"][section] = "result not found in online"
        return course_data

    panes = tabs.find_all("div", class_=re.compile(r"su-tabs-pane"))
    for pane in panes:
        title = pane.get("data-title", "").strip().lower()

        if "entry" in title:
            bullets = element_to_bullets(pane)
            course_data["sections"]["Entry Requirements"] = bullets or "result not found in online"
            print("  ✓ Entry Requirements")
        elif "programme" in title:
            # Prefer Years + Elective Cluster (Marketing / Enterprise System / Human Resource),
            # then other specialized parsers, then generic bulletizer.
            bullets = (
                    programme_structure_with_years_and_clusters(pane)
                    or programme_structure_with_year_groups(pane)
                    or programme_structure_with_mpu_and_elective(pane)
                    or programme_structure_with_mpu(pane)
                    or element_to_bullets(pane)
            )
            course_data["sections"]["Programme Structure"] = bullets or "result not found in online"
            print("  ✓ Programme Structure")

    # Fee section (not always present)
    fee_text = ""
    for fee_keyword in ["fee", "tuition"]:
        fee_block = soup.find(text=re.compile(fee_keyword, re.IGNORECASE))
        if fee_block:
            fee_text = fee_block.parent.get_text(strip=True)
            break
    course_data["sections"]["Fee"] = clean_text(fee_text) if fee_text else "result not found in online"

    return course_data


def get_program_type_and_name(url):
    """Infer program type (Diploma, Degree, etc.) and slug name from URL."""
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.php$", "", slug)
    lower = slug.lower()

    if "foundation" in lower:
        ptype = "Foundation"
    elif "certificate" in lower:
        ptype = "Certificate"
    elif "diploma" in lower:
        ptype = "Diploma"
    elif "bachelor" in lower or "degree" in lower:
        ptype = "Degree"
    else:
        ptype = "Other"

    return ptype, slug


def format_markdown(courses_by_type):
    """Format course data into Markdown."""
    lines = []
    order = ["Certificate", "Foundation", "Diploma", "Degree", "Other"]

    for ptype in order:
        if ptype not in courses_by_type:
            continue

        lines.append(f"# {ptype}\n")
        for course in sorted(courses_by_type[ptype], key=lambda x: x["slug"]):
            lines.append(f"## {course['slug']}\n")
            lines.append("")
            lines.append(f"### URL\n")
            lines.append(f"{course['url']}\n")
            lines.append("")
            for section_title in [
                "Programme Structure",
                "Fee",
                "Duration",
                "Intakes",
                "Entry Requirements",
            ]:
                lines.append(f"### {section_title}\n")
                lines.append(course["sections"].get(section_title, "result not found in online"))
                lines.append("")
            lines.append("---\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Extract Sentral course info to markdown")
    parser.add_argument("input_file", help="Input file containing course URLs")
    parser.add_argument("--output", default="sentral_courses.md", help="Output markdown file")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests (seconds)")
    args = parser.parse_args()

    urls = extract_urls_from_file(args.input_file)
    print(f"Found {len(urls)} course URLs.\n")

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


if __name__ == "__main__":
    exit(main())
