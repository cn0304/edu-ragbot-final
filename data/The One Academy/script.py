#!/usr/bin/env python3
"""
Usage:
    python3 "data/The One Academy/script.py" \
      "data/The One Academy/input.txt" \
      --output "data/The One Academy/Courses.md"

TOA (The One Academy) Course Scraper
Extracts Programme Structure (AREA OF STUDY), Entry Requirements, Intakes, Duration, Location.
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


def extract_urls_from_file(input_file):
    """Extract valid URLs from the txt file."""
    urls = []
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("http"):
                urls.append(line)
    return urls

def toa_study_with_named_groups(study_section):
    hdr_re = re.compile(
        r"""^\s*(
                Course\s+Modules
                |Contextual\s+Courses
                |Software
                |Personal\s+Development
                |Professional\s+Development
                |Media(?:\s+Courses?)?
                |Business\s+Courses?
                |MPU(?:\s+(?:Courses?|Subjects?))?
                |Graphic\s+Design\s+Courses\s+Content
                |Advertising\s+Courses\s+Content
            )\s*$""",
        re.I | re.X
    )
    # Find header nodes in document order
    header_nodes = []
    for node in study_section.find_all(True):
        try:
            txt = clean_text(node.get_text(" ", strip=True))
        except Exception:
            continue
        if hdr_re.match(txt):
            header_nodes.append(node)

    if not header_nodes:
        return ""

    def is_header_node(tag):
        try:
            return bool(hdr_re.match(clean_text(tag.get_text(" ", strip=True))))
        except Exception:
            return False

    def collect_items_from(start_node):
        """Collect all <li> items after start_node until the next header node."""
        items = []
        for sib in start_node.next_siblings:
            # Stop on the next header element/string
            if getattr(sib, "name", None):
                if is_header_node(sib):
                    break
                # accumulate any list items within this sibling (handles multi-column wrappers)
                for li in sib.find_all("li"):
                    t = clean_text(li.get_text(" ", strip=True))
                    if t:
                        items.append(t)
            else:
                s = clean_text(str(sib))
                if hdr_re.match(s):
                    break
        # de-dupe keep order
        seen, out = set(), []
        for it in items:
            k = it.lower()
            if k not in seen:
                seen.add(k)
                out.append(it)
        return out

    lines = []
    for hn in header_nodes:
        title = clean_text(hn.get_text(" ", strip=True))
        items = collect_items_from(hn)
        if items:
            if lines:
                lines.append("")
            lines.append(f"**{title}**")
            lines += [f"- {it}" for it in items]

    return "\n".join(lines).strip()

def extract_course_info(url):
    """Fetch and extract course details from TOA programme page."""
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

    # --- Extract Intake / Duration / Location ---
    info_items = soup.find_all("li")
    intake_text, location_text, duration_text = "", "", ""

    for li in info_items:
        label = li.find("p", class_="upper")
        if not label:
            continue
        label_text = label.get_text(strip=True).lower()
        value_tag = li.find("b")
        value = value_tag.get_text(strip=True) if value_tag else ""
        if "intake" in label_text:
            intake_text = value
        elif "duration" in label_text:
            duration_text = value
        elif "location" in label_text:
            location_text = value

    course_data["sections"]["Intakes"] = clean_text(intake_text) or "result not found in online"
    course_data["sections"]["Duration"] = clean_text(duration_text) or "result not found in online"
    course_data["sections"]["Location"] = clean_text(location_text) or "result not found in online"

    if intake_text:
        print("  ✓ Intakes")
    if duration_text:
        print("  ✓ Duration")
    if location_text:
        print("  ✓ Location")

    # --- Extract Programme Structure (AREA OF STUDY) ---
    study_section = soup.find("section", id="study")
    if study_section:
        # Try named groups first (Course Modules, Contextual Courses, Software, Personal Development)
        ps_text = toa_study_with_named_groups(study_section)

        # Fallback: your previous layout parser
        if not ps_text:
            lines = []
            title = study_section.find("h2")
            if title:
                lines.append(f"**{title.get_text(strip=True)}**")
            for semester in study_section.find_all("div", class_="flex-row"):
                sem_title = semester.get_text(strip=True)
                if sem_title:
                    lines.append(f"\n#### {sem_title}\n")
                ul_list = semester.find_next_sibling("div", class_="ul-list")
                if ul_list:
                    subjects = [li.get_text(strip=True) for li in ul_list.find_all("li")]
                    for subj in subjects:
                        lines.append(f"- {subj}")
            ps_text = "\n".join(lines).strip()

        course_data["sections"]["Programme Structure"] = ps_text or "result not found in online"
        print("  ✓ Programme Structure")
    else:
        course_data["sections"]["Programme Structure"] = "result not found in online"
        print("  ✗ Programme Structure not found")

    # --- Extract Entry Requirements ---
    req_section = soup.find("section", id="requirements")
    if req_section:
        req_list = [li.get_text(strip=True) for li in req_section.find_all("li")]
        text = "\n".join(f"- {li}" for li in req_list)
        course_data["sections"]["Entry Requirements"] = text or "result not found in online"
        print("  ✓ Entry Requirements")
    else:
        course_data["sections"]["Entry Requirements"] = "result not found in online"
        print("  ✗ Entry Requirements not found")

    # --- Fee ---
    course_data["sections"]["Fee"] = "result not found in online"

    return course_data


def get_program_type_and_name(url):
    """Infer program type (Foundation, Diploma, Degree, etc.) and slug name from URL."""
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    lower = slug.lower()

    if "/foundation/" in url:
        ptype = "Foundation"
    elif "/diploma/" in url:
        ptype = "Diploma"
    elif "/degree/" in url:
        ptype = "Degree"
    else:
        ptype = "Other"

    return ptype, slug


def format_markdown(courses_by_type):
    """Format course data into Markdown."""
    lines = []
    order = ["Foundation", "Diploma", "Degree", "Other"]

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
                "Location",
                "Entry Requirements",
            ]:
                lines.append(f"### {section_title}\n")
                lines.append(course["sections"].get(section_title, "result not found in online"))
                lines.append("")
            lines.append("---\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Extract TOA course info to markdown")
    parser.add_argument("input_file", help="Input file containing course URLs")
    parser.add_argument("--output", default="toa_courses.md", help="Output markdown file")
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
