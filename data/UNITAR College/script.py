#!/usr/bin/env python3
"""
Usage:
    python unitar_scraper.py urls.txt --output UnitarCourses.md

Unitar Course Scraper
Extracts Programme Structure, Entry Requirements, Fee, Duration, Intakes.
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
    """Fetch and extract course details from Unitar programme page."""
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

    # --- Programme Structure ---
    structure_section = soup.find("a", string=re.compile("Course Structure", re.I))
    if structure_section:
        ul = structure_section.find_next("ul")
        if ul:
            items = [f"- {clean_text(li.get_text())}" for li in ul.find_all("li")]
            text = "\n".join(items)
            course_data["sections"]["Programme Structure"] = text or "result not found in online"
        else:
            course_data["sections"]["Programme Structure"] = "result not found in online"
        print("  ✓ Programme Structure")
    else:
        course_data["sections"]["Programme Structure"] = "result not found in online"
        print("  ✗ Programme Structure not found")

    # --- Fee ---
    fee_section = soup.find("span", class_=re.compile("month-price", re.I))
    if fee_section:
        amount = fee_section.get_text(strip=True)
        fee_text = f"RM {amount.replace('RM', '').strip()} Total Tuition Fee"
        course_data["sections"]["Fee"] = clean_text(fee_text)
        print("  ✓ Fee")
    else:
        # Try nested divs
        alt_fee = soup.find("div", class_=re.compile("elementor-shortcode", re.I))
        if alt_fee and "Total Tuition Fee" in alt_fee.get_text():
            text = clean_text(alt_fee.get_text())
            match = re.search(r"RM\s?[\d,]+(?:\.\d{2})?", text)
            course_data["sections"]["Fee"] = match.group(0) + " Total Tuition Fee" if match else text
            print("  ✓ Fee (fallback)")
        else:
            course_data["sections"]["Fee"] = "result not found in online"
            print("  ✗ Fee not found")


    # --- Duration ---
    duration_section = soup.find("div", class_=re.compile("programme-duration", re.I))
    if duration_section:
        course_data["sections"]["Duration"] = clean_text(duration_section.get_text())
        print("  ✓ Duration")
    else:
        course_data["sections"]["Duration"] = "result not found in online"
        print("  ✗ Duration not found")

    # --- Intakes ---
    intake_section = soup.find("div", class_=re.compile("programme-intake|intakes-duration", re.I))
    if intake_section:
        li_tags = intake_section.find_all("li")
        if li_tags:
            intakes = ", ".join(clean_text(li.get_text()) for li in li_tags)
            course_data["sections"]["Intakes"] = intakes
        else:
            course_data["sections"]["Intakes"] = clean_text(intake_section.get_text())
        print("  ✓ Intakes")
    else:
        course_data["sections"]["Intakes"] = "result not found in online"
        print("  ✗ Intakes not found")

    # --- Entry Requirements ---
    req_section = soup.find("a", string=re.compile("Entry Requirements", re.I))
    if req_section:
        ul = req_section.find_next("ul")
        if ul:
            items = [f"- {clean_text(li.get_text())}" for li in ul.find_all("li")]
            text = "\n".join(items)
            course_data["sections"]["Entry Requirements"] = text or "result not found in online"
        else:
            course_data["sections"]["Entry Requirements"] = "result not found in online"
        print("  ✓ Entry Requirements")
    else:
        course_data["sections"]["Entry Requirements"] = "result not found in online"
        print("  ✗ Entry Requirements not found")

    return course_data


def get_program_type_and_name(url):
    """Infer program type (Foundation, Diploma, Degree, etc.) and slug name from URL."""
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    lower = slug.lower()

    if "foundation" in lower:
        ptype = "Foundation"
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
    order = ["Foundation", "Diploma", "Degree", "Other"]

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
    parser = argparse.ArgumentParser(description="Extract Unitar course info to markdown")
    parser.add_argument("input_file", help="Input file containing course URLs")
    parser.add_argument("--output", default="UnitarCourses.md", help="Output markdown file")
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
