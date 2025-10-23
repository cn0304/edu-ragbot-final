#!/usr/bin/env python3
"""
RUMC Malaysia Course Information Scraper
Usage:
    python script.py url.txt --output Courses.md

Extracts key course details from RUMC programme pages and formats them into markdown.
Target: https://www.rumc.edu.my/programmes/
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


def extract_course_info(url):
    """Fetch and extract course information from a RUMC page."""
    print(f"Fetching: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
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

    extract_by_id(["course-structure", "programme-delivery", "Learning-Outcomes","teaching-and-learning-methods"], "Programme Structure")
    extract_by_id(["fees"], "Fee")
    extract_by_id(["entry","entry-requirements"], "Entry Requirements")

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
