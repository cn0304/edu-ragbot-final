#!/usr/bin/env python3
"""
Usage:
    python sentral_scraper.py url.txt --output sentral_courses.md

Sentral College Penang Course Scraper
Extracts course details (Programme Structure, Fee, Duration, Entry Requirements, Intakes)
from https://www.sentral.edu.my/programme/ pages and formats them into Markdown.
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


def extract_course_info(url):
    """Fetch and extract course details from Sentral College programme page."""
    print(f"Fetching: {url}")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()
    except Exception as e:
        print(f"  ✗ Error fetching: {e}")
        return None

    soup = BeautifulSoup(res.text, "html.parser")
    course_data = {"url": url, "sections": {}}

    # Extract Intakes and Duration
    duration_block = soup.find("div", class_=re.compile(r"intakes-duration"))
    if duration_block:
        text_block = duration_block.get_text(separator="\n", strip=True)

        # --- Extract Intakes ---
        intakes_section = []
        intakes_ul = duration_block.find("p", string=re.compile("INTAKES", re.IGNORECASE))
        if intakes_ul:
            ul = intakes_ul.find_next("ul")
            if ul:
                for li in ul.find_all("li"):
                    intakes_section.append(clean_text(li.get_text()))
        if intakes_section:
            course_data["sections"]["Intakes"] = ", ".join(intakes_section)
            print("  ✓ Intakes")
        else:
            course_data["sections"]["Intakes"] = "result not found in online"

        # --- Extract Duration ---
        duration_text = ""
        duration_p = duration_block.find("p", string=re.compile("DURATION", re.IGNORECASE))
        if duration_p:
            next_p = duration_p.find_next("p")
            if next_p:
                duration_text = next_p.get_text(strip=True)
        if duration_text:
            course_data["sections"]["Duration"] = clean_text(duration_text)
            print("  ✓ Duration")
        else:
            course_data["sections"]["Duration"] = "result not found in online"
    else:
        course_data["sections"]["Intakes"] = "result not found in online"
        course_data["sections"]["Duration"] = "result not found in online"

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
        content = clean_text(pane.get_text(separator="\n", strip=True))

        if "entry" in title:
            course_data["sections"]["Entry Requirements"] = content or "result not found in online"
            print("  ✓ Entry Requirements")
        elif "programme" in title:
            course_data["sections"]["Programme Structure"] = content or "result not found in online"
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
            courses_by_type[ptype].append({"slug": slug, "sections": info["sections"]})
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
