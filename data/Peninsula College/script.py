#!/usr/bin/env python3
"""
Peninsula College PDF Scraper (Enhanced v2)
Extracts structured course info from PDFs and outputs Markdown.
Usage:
    python script.py pdfs --output courses.md
"""

import os
import re
import argparse
from PyPDF2 import PdfReader
from collections import defaultdict

# -----------------------------------------------------------
# Helper: Read PDF text (skip last page)
# -----------------------------------------------------------
def read_pdf_text(path):
    """Read all text from a PDF except the last page."""
    try:
        reader = PdfReader(path)
        num_pages = len(reader.pages)
        text = ""
        for i in range(num_pages - 1):  # skip last page
            page = reader.pages[i]
            page_text = page.extract_text()
            if page_text:
                text += "\n" + page_text
        return text.strip()
    except Exception as e:
        print(f"Error reading {path}: {e}")
        return ""


# -----------------------------------------------------------
# Helper: Extract text block between keywords
# -----------------------------------------------------------
def extract_block(text, start_keywords, end_keywords=None):
    start_pattern = "|".join([re.escape(k) for k in start_keywords])
    end_pattern = "|".join([re.escape(k) for k in end_keywords]) if end_keywords else "$"
    match = re.search(
        rf"({start_pattern})(.*?)(?={end_pattern})",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(2).strip() if match else None


# -----------------------------------------------------------
# Helper: Clean unwanted patterns
# -----------------------------------------------------------
def clean_text(section, section_name):
    """Clean unwanted noise from extracted section text."""
    if not section:
        return None

    # Remove campus footer lines with URLs and timestamps
    section = re.sub(
        r"\d{2}/\d{2}/\d{4}.*?(Peninsula College).*?(https?://\S+).*?\d+/\d+",
        "",
        section,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove residual weird timestamp or URL fragments
    section = re.sub(r"https?://\S+", "", section)
    section = re.sub(r"\d{2}/\d{2}/\d{4}.*?Peninsula College.*", "", section)

    # For Duration: remove leading "/ Duration"
    if section_name.lower() == "duration":
        section = re.sub(r"^/?\s*Duration\s*", "", section, flags=re.IGNORECASE)

    # For Intake: only keep month names
    if section_name.lower() == "intake":
        months = re.findall(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b",
            section,
            flags=re.IGNORECASE,
        )
        if months:
            section = ", ".join([m.capitalize() for m in months])
        else:
            section = section.strip()

    # Remove redundant blank lines
    section = re.sub(r"\n\s*\n+", "\n", section).strip()
    return section


# -----------------------------------------------------------
# Extract structured info from text
# -----------------------------------------------------------
def extract_course_info(text):
    sections = {}

    prog = extract_block(
        text,
        ["Programme Structure"],
        ["Changes to the programme structure", "Career Prospects", "Download Course Guide"],
    )
    if prog:
        sections["Programme Structure"] = clean_text(prog, "Programme Structure")

    fee = extract_block(text, ["Fee"], ["Entry Requirements", "Programme Structure"])
    sections["Fee"] = clean_text(fee, "Fee") if fee else "result not found in online"

    duration = extract_block(text, ["Study Mode", "Duration"], ["Delivery Location", "Intakes"])
    if duration:
        sections["Duration"] = clean_text(duration, "Duration")

    intake = extract_block(text, ["Intakes"], ["Entry Requirements", "Fee", "Career"])
    if intake:
        sections["Intake"] = clean_text(intake, "Intake")

    entry = extract_block(text, ["Entry Requirements"], ["Career", "Programme Structure"])
    if entry:
        sections["Entry Requirements"] = clean_text(entry, "Entry Requirements")

    return sections


# -----------------------------------------------------------
# Infer program type from filename
# -----------------------------------------------------------
def get_program_type(filename):
    lower = filename.lower()
    if "diploma" in lower:
        return "Diploma"
    elif "degree" in lower or "bachelor" in lower:
        return "Degree"
    elif "master" in lower:
        return "Master"
    elif "phd" in lower or "doctor" in lower:
        return "PhD"
    else:
        return "Other"


# -----------------------------------------------------------
# Slugify: convert "Diploma in Business _ Peninsula College" → "diploma-in-business-_-peninsula-college"
# -----------------------------------------------------------
def slugify(title):
    title = title.strip().lower()
    title = re.sub(r"[\s]+", "-", title)
    title = re.sub(r"[^a-z0-9\-_]", "", title)
    return title


# -----------------------------------------------------------
# Format Markdown Output
# -----------------------------------------------------------
def format_markdown(courses_by_type):
    lines = []
    order = ["Diploma", "Degree", "Master", "PhD", "Other"]

    for ptype in order:
        if ptype not in courses_by_type:
            continue

        lines.append(f"# {ptype}\n")

        for course in sorted(courses_by_type[ptype], key=lambda x: x["name"]):
            slug = slugify(course["name"])
            lines.append(f"## {slug}\n")

            for key in [
                "Programme Structure",
                "Fee",
                "Duration",
                "Intake",
                "Entry Requirements",
            ]:
                lines.append(f"### {key}")
                lines.append("")
                content = course["sections"].get(key, "result not found in online")
                lines.append(content)
                lines.append("")
            lines.append("---\n")

    return "\n".join(lines)


# -----------------------------------------------------------
# Main entry
# -----------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Extract course info from PDFs to Markdown")
    parser.add_argument("pdf_folder", help="Folder containing PDF files")
    parser.add_argument("--output", default="courses.md", help="Output Markdown file")
    args = parser.parse_args()

    folder = args.pdf_folder
    if not os.path.isdir(folder):
        print(f"Error: folder '{folder}' not found")
        return 1

    pdf_files = [
        os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".pdf")
    ]
    if not pdf_files:
        print("No PDF files found.")
        return 1

    courses_by_type = defaultdict(list)

    for i, pdf_path in enumerate(pdf_files, 1):
        filename = os.path.basename(pdf_path)
        print(f"[{i}/{len(pdf_files)}] Reading {filename}")

        text = read_pdf_text(pdf_path)
        if not text:
            print(f"  ✗ Could not extract text")
            continue

        sections = extract_course_info(text)
        ptype = get_program_type(filename)
        name = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
        name = name.replace("_", " ").strip()

        courses_by_type[ptype].append({"name": name, "sections": sections})
        print(f"  ✓ Extracted: {', '.join(sections.keys())}")

    markdown = format_markdown(courses_by_type)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"\n✓ Output written to {args.output}")
    return 0


if __name__ == "__main__":
    exit(main())
