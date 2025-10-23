#!/usr/bin/env python3
"""
Usage:
    python veritas_scraper.py url.txt --output VeritasCourses.md

Veritas University College Course Scraper
Extracts Duration, Pathway, Study Mode, Intakes, Programme Structure,
Entry Requirements, and Fee from each course URL.
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
    """Extract valid URLs from txt file."""
    urls = []
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("http"):
                urls.append(line)
    return urls


def extract_course_info(url):
    """Fetch and extract course details from Veritas course page."""
    print(f"Fetching: {url}")

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()
    except Exception as e:
        print(f"  ✗ Error fetching: {e}")
        return None

    soup = BeautifulSoup(res.text, "html.parser")
    course_data = {"url": url, "sections": {}}

    # --- Duration / Pathway / Study Mode / Intakes ---
    duration = pathway = study_mode = intakes = ""
    info_divs = soup.find_all("div", class_="awa wh")
    for div in info_divs:
        text = div.get_text(" ", strip=True).lower()
        strong = div.find("strong")
        if "duration" in text:
            duration = clean_text(strong.get_text()) if strong else ""
        elif "pathway" in text:
            pathway = clean_text(strong.get_text()) if strong else ""
        elif "study mode" in text:
            study_mode = clean_text(strong.get_text()) if strong else ""
        elif "intakes" in text:
            intakes = clean_text(strong.get_text()) if strong else ""

    course_data["sections"]["Duration"] = duration or "result not found in online"
    course_data["sections"]["Pathway"] = pathway or "result not found in online"
    course_data["sections"]["Study Mode"] = study_mode or "result not found in online"
    course_data["sections"]["Intakes"] = intakes or "result not found in online"

    if duration: print("  ✓ Duration")
    if pathway: print("  ✓ Pathway")
    if study_mode: print("  ✓ Study Mode")
    if intakes: print("  ✓ Intakes")

    # --- Programme Structure ---
    structure_lines = []
    # Try both “Course Modules” h3 and modern Theme-Section variants
    structure_section = soup.find("h2", string=re.compile(r"Course\s*Modules", re.I))
    if not structure_section:
        structure_section = soup.find("div", id=re.compile(r"section-.*"), 
                                      class_=re.compile("Theme-Section.*GridSection"))
    if structure_section:
        # Extract all course year blocks and electives
        for block in structure_section.find_all(["h3", "h4"]):
            title = clean_text(block.get_text())
            if title:
                structure_lines.append(f"#### {title}")
                ul = block.find_next("ul")
                if ul:
                    for li in ul.find_all("li"):
                        structure_lines.append(f"- {clean_text(li.get_text())}")
                structure_lines.append("")
        if not structure_lines:
            # Fallback: capture all li items under GridSection
            for li in structure_section.find_all("li"):
                structure_lines.append(f"- {clean_text(li.get_text())}")
        course_data["sections"]["Programme Structure"] = "\n".join(structure_lines) or "result not found in online"
        print("  ✓ Programme Structure")
    else:
        structure_section = soup.find("h3", string=re.compile(r"Course Modules", re.I))
        if structure_section:
            modules_section = structure_section.find_parent()
            if modules_section:
                for semester_div in modules_section.find_all("div", class_="tab-pane"):
                    sem_title = semester_div.get("id", "").replace("-", " ").title()
                    if sem_title:
                        structure_lines.append(f"#### {sem_title}")
                    for li in semester_div.find_all("li"):
                        structure_lines.append(f"- {clean_text(li.get_text())}")
                    structure_lines.append("")
            course_data["sections"]["Programme Structure"] = "\n".join(structure_lines) or "result not found in online"
            print("  ✓ Programme Structure")
        else:

            # --- Additional Conditions for Programme Structure ---
            if not structure_lines:
                # Condition A: LL.M style tabbed modules
                pix_tabs = soup.find("div", class_=re.compile(r"pix_tabs_container"))
                if pix_tabs:
                    tab_buttons = pix_tabs.find_all("a", class_=re.compile("pix-tabs-btn"))
                    for btn in tab_buttons:
                        tab_title = clean_text(btn.get_text())
                        if tab_title:
                            structure_lines.append(f"#### {tab_title}")
                            tab_id = btn.get("href", "").replace("#", "")
                            tab_content = soup.find("div", id=tab_id)
                            if tab_content:
                                for block in tab_content.find_all("div", style=re.compile("line-height")):
                                    title_tag = block.find("p")
                                    if title_tag and title_tag.find("strong"):
                                        title = clean_text(title_tag.get_text())
                                        desc_tag = title_tag.find_next_sibling("p")
                                        desc = clean_text(desc_tag.get_text()) if desc_tag else ""
                                        structure_lines.append(f"- **{title}** — {desc}")
                                structure_lines.append("")

                # Condition B: Theme-Section modern structure (Year-based)
                if not structure_lines:
                    theme_sections = soup.find_all("div", class_=re.compile(r"Theme-Section Theme-TextSection"))
                    for section in theme_sections:
                        if section.find("h2", string=re.compile("Course Modules", re.I)):
                            for h3 in section.find_all("h3"):
                                year = clean_text(h3.get_text())
                                if year:
                                    structure_lines.append(f"#### {year}")
                                    # Get all h4 and their paragraphs within same section
                                    for h4 in section.find_all("h4"):
                                        title = clean_text(h4.get_text())
                                        desc_tag = h4.find_next_sibling("p")
                                        desc = clean_text(desc_tag.get_text()) if desc_tag else ""
                                        structure_lines.append(f"- **{title}** — {desc}")
                                    structure_lines.append("")
                
                # Condition C: Continuation Theme-Section (Year 2, Year 3, MPU)
                if not structure_lines:
                    for section in soup.find_all("div", class_=re.compile(r"Theme-Section Theme-TextSection")):
                        for h3 in section.find_all("h3"):
                            year = clean_text(h3.get_text())
                            if year:
                                structure_lines.append(f"#### {year}")
                                for h4 in section.find_all("h4"):
                                    title = clean_text(h4.get_text())
                                    desc_tag = h4.find_next_sibling("p")
                                    desc = clean_text(desc_tag.get_text()) if desc_tag else ""
                                    structure_lines.append(f"- **{title}** — {desc}")
                                structure_lines.append("")
                # Condition D: Veritas-style course list (core the-course-offer)
                if not structure_lines:
                    course_offers = soup.find_all("div", class_=re.compile(r"the-course-offer"))
                    if course_offers:
                        structure_lines.append("#### Programme Modules")
                        for course in course_offers:
                            title_tag = course.find("h4")
                            uni_tag = course.find("h5")
                            desc_tag = course.find("p")

                            title = clean_text(title_tag.get_text()) if title_tag else ""
                            uni = clean_text(uni_tag.get_text()) if uni_tag else ""
                            desc = clean_text(desc_tag.get_text()) if desc_tag else ""

                            # Format neatly for Markdown
                            line = f"- **{title}**"
                            if uni:
                                line += f" ({uni})"
                            if desc:
                                line += f" — {desc}"
                            structure_lines.append(line)

                        structure_lines.append("")

            if structure_lines:
                course_data["sections"]["Programme Structure"] = "\n".join(structure_lines)
                print("  ✓ Programme Structure")
            else:
                course_data["sections"]["Programme Structure"] = "result not found in online"
                print("  ✗ Programme Structure not found")



    # --- Entry Requirements ---
    entry_lines = []
    entry_section = None

    # Old style: <h2>How Do I Get In?</h2>
    for h2 in soup.find_all("h2"):
        if "how do i get in" in h2.get_text(strip=True).lower():
            entry_section = h2.find_parent()
            break

    # New style: Theme-Layer-BodyText containing “How Do I Get In?”
    if not entry_section:
        for div in soup.find_all("div", class_=re.compile("Theme-Layer-BodyText")):
            text = div.get_text(strip=True)
            if re.search(r"how do i get in|entry", text, re.I):
                entry_section = div
                break
    # If not found, try Unitar-style (HOW DO I GET IN?)
    if not entry_section:
        # Find any div that might contain "HOW DO I GET IN" or "ENTRY REQUIREMENTS"
        for div in soup.find_all("div", class_=re.compile("wpb_text_column|Theme-Layer-BodyText", re.I)):
            text = div.get_text(" ", strip=True)
            if re.search(r"how do i get in|entry\s*requirements", text, re.I):
                entry_section = div
                break

    if entry_section:
        for li in entry_section.find_all("li"):
            entry_lines.append(f"- {clean_text(li.get_text())}")
        if not entry_lines:
            for p in entry_section.find_all("p"):
                entry_lines.append(f"- {clean_text(p.get_text())}")
        course_data["sections"]["Entry Requirements"] = "\n".join(entry_lines) or "result not found in online"
        print("  ✓ Entry Requirements")
    else:
        course_data["sections"]["Entry Requirements"] = "result not found in online"
        print("  ✗ Entry Requirements not found")

    # --- Fee (image or table) ---
    fee_content = ""

    # Try <table> (new site layout)
    fee_table = soup.find("table", class_=re.compile("Theme-Layer-BodyText-Table"))
    if fee_table:
        rows = []
        for tr in fee_table.find_all("tr"):
            cols = [clean_text(td.get_text()) for td in tr.find_all(["th", "td"])]
            if cols:
                rows.append(" | ".join(cols))
        if rows:
            header = rows[0]
            separator = " | ".join(["---"] * len(header.split("|")))
            fee_content = "\n".join([header, separator] + rows[1:])
            print("  ✓ Fee (table)")
    else:
        # Try <img> fallback
        fee_img = None
        for img in soup.find_all("img"):
            alt = img.get("alt", "").lower()
            src = img.get("src", "")
            if "fee" in alt or "fee" in src.lower():
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://www.veritas.edu.my" + src
                fee_img = src
                break
        if fee_img:
            fee_content = f"![Fee Structure]({fee_img})"
            print("  ✓ Fee (image)")
        else:
            print("  ✗ Fee not found")

    course_data["sections"]["Fee"] = fee_content or "result not found in online"

    return course_data



def get_program_type_and_name(url):
    """Infer program type and slug from URL."""
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    lower = slug.lower()
    if "foundation" in lower:
        ptype = "Foundation"
    elif "diploma" in lower:
        ptype = "Diploma"
    elif "degree" in lower or "bachelor" in lower or "bsc" in lower or "ba-hons" in lower:
        ptype = "Degree"
    elif "master" in lower:
        ptype = "Master"
    elif "phd" in lower or "doctor" in lower:
        ptype = "PHD"
    else:
        ptype = "Master"
    return ptype, slug


def format_markdown(courses_by_type):
    """Format course data into Markdown."""
    lines = []
    order = ["Foundation", "Diploma", "Degree", "Master", "PHD", "Other"]

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
            for section in [
                "Duration",
                "Pathway",
                "Study Mode",
                "Intakes",
                "Programme Structure",
                "Entry Requirements",
                "Fee",
            ]:
                lines.append(f"### {section}\n")
                lines.append(course["sections"].get(section, "result not found in online"))
                lines.append("")
            lines.append("---\n")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Veritas course scraper")
    parser.add_argument("input_file", help="Text file containing course URLs")
    parser.add_argument("--output", default="VeritasCourses.md", help="Output markdown file")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests (seconds)")
    args = parser.parse_args()

    urls = extract_urls_from_file(args.input_file)
    print(f"Found {len(urls)} course URLs.\n")

    courses_by_type = defaultdict(list)
    success, fail = 0, 0

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}]")
        info = extract_course_info(url)
        if info:
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
