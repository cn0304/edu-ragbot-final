#!/usr/bin/env python3
"""
python script.py input.txt --output Courses.md
MSU Malaysia Course Information Scraper
Extracts course details from MSU Malaysia website and formats them into markdown.
https://www.msu.edu.my/programme
"""

import argparse
import re
import time
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from collections import defaultdict


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

    # Find all major content blocks
    content_blocks = soup.find_all("div", class_=re.compile(r"mb-3"))
    rows = soup.find_all("div", class_="row")

    def extract_block(title_keywords):
        """Helper: extract text from a block that contains certain keywords."""
        for div in content_blocks + rows:
            heading = div.find(["h5", "a"])
            if heading:
                heading_text = heading.get_text(strip=True).lower()
                if any(kw in heading_text for kw in title_keywords):
                    text = div.get_text(separator="\n", strip=True)
                    return clean_text(text)
        return ""

    # Extract key sections
    about = extract_block(["about"])
    entry = extract_block(["entry"])
    duration = extract_block(["duration"])
    fee = extract_block(["fee"])
    programme = extract_block(["courses offered", "programme structure"])
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
    order = ["Diploma", "Degree", "Master", "PhD", "Other"]

    for ptype in order:
        if ptype not in courses_by_type:
            continue

        lines.append(f"# {ptype}")
        lines.append("")

        for course in sorted(courses_by_type[ptype], key=lambda x: x["slug"]):
            lines.append(f"## {course['slug']}")
            lines.append("")

            # ✅ Add URL section
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

    # Read HTML file
    try:
        with open(args.input_file, "r", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        print(f"Error: {args.input_file} not found")
        return 1

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
