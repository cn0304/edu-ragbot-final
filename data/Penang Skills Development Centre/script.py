#!/usr/bin/env python3
"""
FutureTech College Course Information Scraper
Usage:
    python script.py url.txt --output Courses.md

This script extracts course details from FutureTech College programme pages.
https://www.futuretech.edu.my/programs/
"""

import argparse
import re
import time
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from collections import defaultdict


def extract_urls_from_text(file_content):
    """Extract all course URLs from input text file."""
    urls = []
    for line in file_content.splitlines():
        line = line.strip()
        if line.startswith("http") and "futuretech.edu.my" in line:
            urls.append(line)
    return urls


def clean_text(text):
    """Normalize whitespace."""
    return re.sub(r"\s+", " ", text).strip() if text else ""


def extract_course_info(url):
    """Fetch and parse a single course page."""
    print(f"Fetching: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    }

    try:
        res = requests.get(url, headers=headers, timeout=20)
        res.raise_for_status()
    except Exception as e:
        print(f"  ✗ Error fetching: {e}")
        return None

    soup = BeautifulSoup(res.text, "html.parser")
    course_data = {"url": url, "sections": {}}

    # Extract Course Title (h2)
    title_tag = soup.find("h2")
    course_data["title"] = clean_text(title_tag.text) if title_tag else "Untitled Course"

    # === Duration, Intake, Mode ===
    details = soup.find_all("div", class_="feature-sin")
    for d in details:
        txt = d.get_text(" ", strip=True)
        if "Duration" in txt:
            duration = txt.replace("Duration", "").strip()
            course_data["sections"]["Duration"] = duration
            print("  ✓ Duration")
        elif "Intake" in txt:
            intake = txt.replace("Intake", "").strip()
            course_data["sections"]["Intake"] = intake
            print("  ✓ Intake")

    # === Programme Structure (Course Content) ===
    course_content = soup.find("div", id="coursecontent")
    if course_content:
        lists = course_content.find_all("ul")
        content_items = []
        for ul in lists:
            content_items.extend([clean_text(li.get_text()) for li in ul.find_all("li")])
        if content_items:
            joined = "\n".join(f"- {item}" for item in content_items)
            course_data["sections"]["Programme Structure"] = joined
            print("  ✓ Programme Structure")

    # === Entry Requirements ===
    details_box = soup.find("div", id="details")
    if details_box:
        sections = []
        for sec in details_box.find_all("div", class_="section-title"):
            header = clean_text(sec.get_text())
            ul = sec.find_next_sibling("ul")
            if ul:
                items = [f"- {clean_text(li.get_text())}" for li in ul.find_all("li")]
                if items:
                    sections.append(f"**{header}**\n" + "\n".join(items))
        if sections:
            course_data["sections"]["Entry Requirements"] = "\n\n".join(sections)
            print("  ✓ Entry Requirements")

    # === Fee (not shown on FutureTech site) ===
    course_data["sections"]["Fee"] = "Result not found in online"
    print("  ✗ Fee not found")

    return course_data


def get_program_type_and_name(url):
    """Infer program type (Certificate, Diploma, Degree, etc.) from URL."""
    lower = url.lower()
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    if "certificate" in lower:
        ptype = "Certificate"
    elif "diploma" in lower:
        ptype = "Diploma"
    elif "degree" in lower or "bachelor" in lower:
        ptype = "Degree"
    elif "master" in lower:
        ptype = "Master"
    else:
        ptype = "Other"
    return ptype, slug


def format_markdown(courses_by_type):
    """Format results into markdown."""
    lines = []
    order = ["Certificate", "Diploma", "Degree", "Master", "Other"]

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
                "Intake",
                "Entry Requirements",
            ]:
                if section_title in course["sections"]:
                    lines.append(f"### {section_title}\n")
                    lines.append(course["sections"][section_title])
                    lines.append("")
            lines.append("---\n")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="FutureTech Course Scraper")
    parser.add_argument("input_file", help="Text file containing FutureTech URLs")
    parser.add_argument("--output", default="Courses.md", help="Output markdown file")
    parser.add_argument("--delay", type=float, default=1.2, help="Delay between requests")
    args = parser.parse_args()

    try:
        with open(args.input_file, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: {args.input_file} not found")
        return 1

    urls = extract_urls_from_text(content)
    print(f"Found {len(urls)} course URLs.\n")

    courses_by_type = defaultdict(list)
    success, fail = 0, 0

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}]")
        info = extract_course_info(url)
        if info:
            ptype, slug = get_program_type_and_name(url)
            courses_by_type[ptype].append({"slug": slug, "sections": info["sections"]})
            success += 1
        else:
            fail += 1
        if i < len(urls):
            time.sleep(args.delay)

    print(f"\nCompleted: {success} success, {fail} failed.\n")

    if success > 0:
        md = format_markdown(courses_by_type)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"✓ Output written to {args.output}")
    else:
        print("No successful extractions.")
    return 0


if __name__ == "__main__":
    exit(main())
