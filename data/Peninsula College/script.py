#!/usr/bin/env python3
"""
Peninsula College PDF Scraper (Enhanced v2)
Extracts structured course info from PDFs and outputs Markdown.

Usage:
    python3 "data/Peninsula College/script.py" \
      "data/Peninsula College/pdfs" \
      --output "data/Peninsula College/Courses.md"
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
# Helper: Strip footer/page artifacts inside Programme Structure
# -----------------------------------------------------------
def _strip_progstruct_footers(text: str) -> str:
    cleaned = []
    for line in text.splitlines():
        s = line.strip()

        # 1) Lines starting with /date or /page-ish tokens (often after a bullet)
        if re.match(r'^[-•\u2022]?\s*/\d{1,2}(?:/\d{1,2})?(?:/\d{2,4})?(?:,\s*\d{1,2}:\d{2})?.*$', s):
            continue
        if re.match(r'^[-•\u2022]?\s*\d+\s*/\s*\d+\s*$', s, flags=re.IGNORECASE):  # like "5/12"
            continue
        if re.match(r'^[-•\u2022]?\s*/\d+\s*$', s):  # like "/5"
            continue
        if re.match(r'^[-•\u2022]?\s*page\s+\d+(?:\s*/\s*\d+)?\s*$', s, flags=re.IGNORECASE):
            continue

        # 2) Lines with programme name + " | The Ship Campus"
        if re.search(r'\|\s*the\s+ship\s+campus\b', s, flags=re.IGNORECASE):
            continue

        # 3) Timestampy footer lines that also mention the college
        if re.search(r'peninsula\s+college', s, flags=re.IGNORECASE) and re.search(r'\d{1,2}:\d{2}', s):
            continue

        cleaned.append(line)
    return "\n".join(cleaned)

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
        prog = clean_text(prog, "Programme Structure")
        prog = _strip_progstruct_footers(prog)
        sections["Programme Structure"] = prog

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
# Helper: Format Programme Structure into bold Year/Elective blocks with bullets
# -----------------------------------------------------------
def format_programme_structure(section_text: str) -> str:
    if not section_text:
        return section_text

    lines = [l.strip() for l in section_text.splitlines() if l.strip()]
    out = []
    current_block = None  # 'year', 'elective', 'compulsory', 'months', or None

    for raw in lines:
        # Trim common bullet prefixes first (•, -, numbers, etc.)
        core = re.sub(r'^[\u2022•\-\–\—\d\.\)\(]+\s*', '', raw).strip()
        # For matching headers without trailing *** etc.
        core_no_stars = re.sub(r'^\*+|\*+$', '', core).strip()

        # 1) Year header
        m_year = re.match(r'^(year\s*\d+)\b', core_no_stars, flags=re.IGNORECASE)
        if m_year:
            if out and out[-1] != "":
                out.append("")
            out.append(f"**{m_year.group(1).title()}**")
            out.append("")
            current_block = 'year'
            continue

        # 2) "X Months" header (e.g., "4 Months")
        m_months = re.match(r'^(\d{1,2})\s*months?$', core_no_stars, flags=re.IGNORECASE)
        if m_months:
            if out and out[-1] != "":
                out.append("")
            out.append(f"**{m_months.group(1)} Months**")
            out.append("")
            current_block = 'months'
            continue

        # 2a) Orphan "Months" line right after a bullet that is just a number (PDF split: "4" then "Months")
        if re.match(r'^months?$', core_no_stars, flags=re.IGNORECASE):
            if out and re.match(r'^\-\s*(\d{1,2})\s*$', out[-1] or ""):
                num = re.match(r'^\-\s*(\d{1,2})\s*$', out[-1]).group(1)
                out.pop()  # remove the "- 4" bullet
                if out and out[-1] != "":
                    out.append("")
                out.append(f"**{num} Months**")
                out.append("")
                current_block = 'months'
                continue
            # If we can't find the number, still treat as a header "Months"
            if out and out[-1] != "":
                out.append("")
            out.append("**Months**")
            out.append("")
            current_block = 'months'
            continue

        # 3) Real section headers: "Elective Subject(s)" or "Compulsory Subject(s)" -> ALWAYS bold
        if re.match(r'^(elective|compulsory)\s+subject(s)?$', core_no_stars, flags=re.IGNORECASE):
            if out and out[-1] != "":
                out.append("")
            out.append(f"**{core_no_stars.title()}**")
            out.append("")
            current_block = 'elective' if core_no_stars.lower().startswith('elective') else 'compulsory'
            continue

        # 3a) Plain "Elective" (standalone header) -> bold
        if re.match(r'^elective(?:\s*subjects?)?$', core_no_stars, flags=re.IGNORECASE):
            if out and out[-1] != "":
                out.append("")
            out.append("**Elective**")
            out.append("")
            current_block = 'elective'
            continue

        # 4) Lines like "Elective (1)***", "Elective 2" -> bullets (never bold under a Year)
        if re.match(r'^elective\b', core_no_stars, flags=re.IGNORECASE):
            item = re.sub(r'\*+$', '', core_no_stars).strip()
            out.append(f"- {item}")
            continue

        # 5) Normal subject line -> bullet
        item = re.sub(r'\*+$', '', core).strip()
        if item:
            out.append(f"- {item}")

    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result
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
    # Remove trailing campus/branding phrases before hyphenizing
    title = re.sub(r'[-\s_]*(the[\s\-_]+ship[\s\-_]+campus|ship[\s\-_]+campus)\s*$',
                   '', title, flags=re.IGNORECASE)
    title = re.sub(r'[-\s_]*((?:malaysia[\s\-_]+)?peninsula[\s\-_]+college)\s*$',
                   '', title, flags=re.IGNORECASE)
    title = re.sub(r'[-\s_]*(peninsula[\s\-_]+college)\s*$',
                   '', title, flags=re.IGNORECASE)

    title = re.sub(r"[\s]+", "-", title)
    title = re.sub(r"[^a-z0-9\-_]", "", title)
    title = re.sub(r"-{2,}", "-", title).strip("-")
    return title


# -----------------------------------------------------------
# Helper: Clean program name (drop campus suffixes)
# -----------------------------------------------------------
def clean_program_name(name: str) -> str:
    s = name

    # Normalise separators for matching (spaces, hyphens, underscores)
    # but WITHOUT changing the original s except for our removals
    # We’ll match with [\s\-_]+ between words.
    # 1) " - The Ship Campus", "(The Ship Campus)", " The Ship Campus"
    s = re.sub(r'\s*[-–—]\s*(the[\s\-_]+ship[\s\-_]+campus|ship[\s\-_]+campus)\s*$',
               '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*\((the[\s\-_]+ship[\s\-_]+campus|ship[\s\-_]+campus)\)\s*$',
               '', s, flags=re.IGNORECASE)
    s = re.sub(r'[\s\-_]+(the[\s\-_]+ship[\s\-_]+campus|ship[\s\-_]+campus)\s*$',
               '', s, flags=re.IGNORECASE)

    # 2) "... - Malaysia Peninsula College", "(Malaysia Peninsula College)",
    #    or just "... Malaysia Peninsula College" at the end
    s = re.sub(r'\s*[-–—]\s*((?:malaysia[\s\-_]+)?peninsula[\s\-_]+college)\s*$',
               '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*\(((?:malaysia[\s\-_]+)?peninsula[\s\-_]+college)\)\s*$',
               '', s, flags=re.IGNORECASE)
    s = re.sub(r'[\s\-_]+((?:malaysia[\s\-_]+)?peninsula[\s\-_]+college)\s*$',
               '', s, flags=re.IGNORECASE)

    # Also handle just trailing " - Peninsula College"
    s = re.sub(r'\s*[-–—]\s*(peninsula[\s\-_]+college)\s*$',
               '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*\((peninsula[\s\-_]+college)\)\s*$',
               '', s, flags=re.IGNORECASE)
    s = re.sub(r'[\s\-_]+(peninsula[\s\-_]+college)\s*$',
               '', s, flags=re.IGNORECASE)

    # Collapse extra spaces
    s = re.sub(r'\s{2,}', ' ', s).strip()
    return s


# -----------------------------------------------------------
# Mapping: Peninsula course slug -> official web URL
# (slug is generated by slugify(course["name"]))
# -----------------------------------------------------------
PENINSULA_URLS = {
    "certificate-in-business-studies": "https://peninsulacollege.edu.my/certificate-in-business-studies-the-ship-campus-2/",
    "foundation-in-arts": "https://peninsulacollege.edu.my/foundation-in-arts-the-ship-campus/",
    "foundation-in-science": "https://peninsulacollege.edu.my/foundation-in-science-the-ship-campus/",
    "diploma-in-logistics-management": "https://peninsulacollege.edu.my/diploma-in-logistics-management-the-ship-campus-2/",
    "diploma-in-business": "https://peninsulacollege.edu.my/diploma-in-business-studies-the-ship-campus/",
    "diploma-in-e-business-technology": "https://peninsulacollege.edu.my/diploma-in-e-business-technology-the-ship-campus/",
    "diploma-in-computer-science": "https://peninsulacollege.edu.my/diploma-in-computer-science-the-ship-campus/",
    "diploma-in-electrical-and-electronics-engineering-course-malaysia": "https://peninsulacollege.edu.my/diploma-in-electrical-and-electronics-engineering-technology-the-ship-campus/",
    "diploma-of-accountancy": "https://peninsulacollege.edu.my/diploma-of-accountancy-the-ship-campus/",
    "ba-hons-accounting-financial": "https://peninsulacollege.edu.my/ba-hons-accounting-and-financial-accounting-the-ship-campus/",
    "bsc-maritime-logistics-management": "https://peninsulacollege.edu.my/bsc-hons-maritime-business-logistics-the-ship-campus/",
    "bsc-hons-business-management": "https://peninsulacollege.edu.my/bsc-hons-business-management-the-ship-campus/",
    "bsc-hons-computer-science-cyber-security": "https://peninsulacollege.edu.my/bsc-hons-computer-science-cyber-security-the-ship-campus/",
    "bsc-hons-computer-science-software-engineering": "https://peninsulacollege.edu.my/bsc-hons-computer-science-software-engineering-the-ship-campus/",
}
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

            # 🔹 URL section (added)
            url = PENINSULA_URLS.get(slug, "result not found in online")
            lines.append("### URL")
            lines.append("")
            lines.append(url)
            lines.append("")

            # Existing sections
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
                if key == "Programme Structure" and content != "result not found in online":
                    content = format_programme_structure(content)
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
        name = clean_program_name(name)

        courses_by_type[ptype].append({"name": name, "sections": sections})
        print(f"  ✓ Extracted: {', '.join(sections.keys())}")

    markdown = format_markdown(courses_by_type)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(markdown)

    print(f"\n✓ Output written to {args.output}")
    return 0


if __name__ == "__main__":
    exit(main())
