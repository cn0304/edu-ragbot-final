#!/usr/bin/env python3
"""
python script.py url.txt --output TARC_Courses.md
TARC Course Information Scraper
Extracts course details from TARC Google Sites and formats them into markdown.
"""

import argparse
import re
import time
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from collections import defaultdict


def extract_urls_from_file(filepath):
    """Extract all course URLs from the input file."""
    urls = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if line and not line.startswith("#"):
                    if line.startswith("http"):
                        urls.append(line)
    except Exception as e:
        print(f"Error reading file: {e}")
    return urls


def clean_text(text):
    """Clean and normalize text."""
    if not text:
        return ""
    # Remove excessive whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove inline styles and other noise
    text = re.sub(r'style="[^"]*"', '', text)
    return text.strip()


def normalize_fee_text(text):
    """Normalize fee text by fixing broken numbers."""
    # Fix patterns like "RM 37 , 2 00" -> "RM 37,200"
    # Match: number + space + comma + space + number(s)
    text = re.sub(r'(\d+)\s*,\s*(\d+)', r'\1,\2', text)
    # Fix "RM 37" when it should be part of larger number
    # This is trickier - look for incomplete patterns
    return text


def extract_section_by_heading(text_lines, heading_keywords, stop_keywords):
    """
    Extract content between a heading and stop keywords.
    
    Args:
        text_lines: List of text lines
        heading_keywords: Keywords to identify the start (e.g., ["intake", "intakes"])
        stop_keywords: Keywords to identify the end (e.g., ["duration", "fees"])
    
    Returns:
        List of extracted lines
    """
    result = []
    capturing = False
    
    for line in text_lines:
        line_lower = line.lower()
        
        # Check if we hit a heading
        if any(kw in line_lower for kw in heading_keywords):
            capturing = True
            # Include the heading line if it has substantial content
            if len(line) > 50 or any(word in line_lower for word in ["year", "intake", "duration", "rm "]):
                result.append(line)
            continue
        
        # Check if we should stop
        if capturing and any(kw in line_lower for kw in stop_keywords):
            break
        
        # Capture content
        if capturing and line and len(line) > 2:
            # Skip navigation elements
            if not any(skip in line_lower for skip in ["click here", "apply now", "looking for more", "expand", "collapse"]):
                result.append(line)
    
    return result


def extract_fee_info(soup, lines):
    """
    Extract fee information more carefully by looking at HTML structure.
    This handles cases where numbers are split across multiple spans.
    """
    fee_content = []
    
    # Strategy 1: Find all <p> or <div> tags that contain "Estimated Total Fees"
    fee_elements = soup.find_all(string=re.compile(r'Estimated Total Fees', re.IGNORECASE))
    
    for fee_elem in fee_elements:
        # Get the parent paragraph/div
        parent = fee_elem.find_parent(['p', 'div', 'span'])
        if parent:
            # Get the full parent container
            container = parent.find_parent(['p', 'div'])
            if container:
                # Extract text from this container and next siblings
                current = container
                collected_text = []
                
                for _ in range(5):  # Check up to 5 sibling elements
                    if current:
                        text = current.get_text(separator=" ", strip=True)
                        text = normalize_fee_text(text)
                        
                        # Stop at scholarship mentions
                        if any(stop in text.lower() for stop in ["merit scholarship", "other scholarship", "financial aid", "for ptptn"]):
                            break
                        
                        if text and len(text) > 5:
                            collected_text.append(text)
                        
                        current = current.find_next_sibling()
                    else:
                        break
                
                if collected_text:
                    return collected_text
    
    # Strategy 2: Fall back to line-based extraction
    for i, line in enumerate(lines):
        if "estimated total fees" in line.lower():
            # Collect this line and next few lines
            for j in range(i, min(i + 8, len(lines))):
                current_line = lines[j]
                
                # Stop at scholarship mentions
                if any(stop in current_line.lower() for stop in ["merit scholarship", "other scholarship", "financial aid", "for ptptn", "to find out more"]):
                    break
                
                # Include lines with fee information
                if any(indicator in current_line.lower() for indicator in ["rm", "estimated", "fees", "may vary", "note:", "sst", "malaysian", "international"]):
                    # Normalize the fee text
                    normalized = normalize_fee_text(current_line)
                    # Skip "click here" and similar
                    if "click here" not in normalized.lower() and "for more information" not in normalized.lower():
                        fee_content.append(normalized)
            break
    
    # Clean up collected fees
    cleaned_fees = []
    for fee_line in fee_content:
        # Skip empty or too short
        if len(fee_line.strip()) < 5:
            continue
        # Skip lines that are just "Fees" or headers
        if fee_line.strip().lower() in ["fees", "fees:", "estimated fees"]:
            continue
        cleaned_fees.append(fee_line.strip())
    
    return cleaned_fees[:6]  # Limit to first 6 relevant lines


def extract_course_info(url):
    """Fetch and extract key course information from TARC course page."""
    print(f"Fetching: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()
    except Exception as e:
        print(f"  ✗ Error fetching: {e}")
        return None

    soup = BeautifulSoup(res.text, "html.parser")
    course_data = {"url": url, "sections": {}}
    
    # Get full text for easier parsing
    full_text = soup.get_text(separator="\n", strip=True)
    lines = [clean_text(line) for line in full_text.split("\n") if clean_text(line)]

    # ============ Extract Programme Outline/Structure ============
    programme_lines = extract_section_by_heading(
        lines,
        heading_keywords=["programme outline", "common courses", "specialisation courses"],
        stop_keywords=["campus:", "minimum entry requirement", "intake:", "duration:", "estimated total fees", "fees & financial aid"]
    )
    
    if programme_lines:
        course_data["sections"]["Programme Structure"] = "\n".join(programme_lines)
        print("  ✓ Programme Structure")
    else:
        print("  ✗ Programme Structure not found")

    # ============ Extract Fees ============
    fee_content = extract_fee_info(soup, lines)
    
    if fee_content:
        course_data["sections"]["Fee"] = "\n".join(fee_content)
        print("  ✓ Fee")
    else:
        course_data["sections"]["Fee"] = "You can find fee information online"
        print("  ✗ Fee (not found - using fallback)")
    # # Look for fees in multiple patterns
    # fee_content = []
    
    # # Pattern 1: Direct "Estimated Total Fees" line
    # for i, line in enumerate(lines):
    #     if "estimated total fees" in line.lower():
    #         # Get this line and next few lines
    #         for j in range(i, min(i + 10, len(lines))):
    #             current_line = lines[j]
    #             # Stop at certain keywords
    #             if j > i and any(kw in current_line.lower() for kw in ["merit scholarship", "for more information", "note:", "click here", "financial aid", "looking for"]):
    #                 break
    #             if "rm" in current_line.lower() or "estimated" in current_line.lower() or "fees" in current_line.lower() or "may vary" in current_line.lower() or "sst" in current_line.lower():
    #                 fee_content.append(current_line)
    #         break
    
    # # Pattern 2: Look in "Fees & Financial Aid" section
    # if not fee_content:
    #     fee_lines = extract_section_by_heading(
    #         lines,
    #         heading_keywords=["fees & financial aid", "fees and financial aid"],
    #         stop_keywords=["merit scholarship", "looking for more", "minimum entry"]
    #     )
    #     # Get only the fee information, not scholarship links
    #     for line in fee_lines:
    #         if "rm" in line.lower() or "estimated" in line.lower() or "vary" in line.lower() or "sst" in line.lower():
    #             fee_content.append(line)
    #             if len(fee_content) >= 3:  # Typically 2-3 lines for fees
    #                 break
    
    # if fee_content:
    #     course_data["sections"]["Fee"] = "\n".join(fee_content)
    #     print("  ✓ Fee")
    # else:
    #     course_data["sections"]["Fee"] = "You can find fee information online at:\nhttps://www.tarc.edu.my/bursary/malaysian-student-fees-guide/"
    #     print("  ✗ Fee (not found - using fallback)")

    # ============ Extract Duration ============
    duration_content = []
    for i, line in enumerate(lines):
        if line.lower().strip() == "duration:" or line.lower().startswith("duration:"):
            # Get the next line(s) with actual duration
            for j in range(i, min(i + 3, len(lines))):
                if j == i and ":" in lines[j]:
                    # Duration on same line
                    duration_text = lines[j].split(":", 1)[1].strip()
                    if duration_text:
                        duration_content.append(duration_text)
                        break
                elif j > i and lines[j]:
                    # Duration on next line
                    if not any(kw in lines[j].lower() for kw in ["intake", "fees", "estimated", "campus"]):
                        duration_content.append(lines[j])
                        break
            break
    
    if duration_content:
        course_data["sections"]["Duration"] = "\n".join(duration_content)
        print("  ✓ Duration")
    else:
        print("  ✗ Duration not found")

    # ============ Extract Intakes ============
    intake_content = []
    
    for i, line in enumerate(lines):
        if line.lower().strip() == "intake:" or "intake:" in line.lower():
            # Capture everything until we hit duration or fees
            for j in range(i, min(i + 15, len(lines))):
                current_line = lines[j]
                
                # Stop conditions
                if j > i and any(kw in current_line.lower() for kw in ["duration:", "estimated total fees", "campus:", "minimum entry"]):
                    break
                
                # Include relevant lines
                if j == i:
                    # First line - include if it has content after "Intake:"
                    if ":" in current_line:
                        after_colon = current_line.split(":", 1)[1].strip()
                        if after_colon and len(after_colon) > 5:
                            intake_content.append(current_line)
                    else:
                        intake_content.append(current_line)
                else:
                    # Subsequent lines - include if they look like intake info
                    if any(indicator in current_line.lower() for indicator in ["year ", "intake", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "kuala lumpur", " kl", "penang", " pg"]):
                        intake_content.append(current_line)
                    elif current_line and len(current_line) < 50 and not any(skip in current_line.lower() for skip in ["click", "http", "expand", "collapse"]):
                        # Short lines that might be intake dates
                        intake_content.append(current_line)
            break
    # Extract Intakes
    if not intake_content:
        for i, line in enumerate(lines):
            if "intakes:" in line.lower():
                # Get next few lines
                for j in range(i+1, min(i+5, len(lines))):
                    if "duration:" in lines[j].lower() or "estimated" in lines[j].lower():
                        break
                    if lines[j] and not lines[j].startswith("http"):
                        intake_content.append(lines[j])
    if intake_content:
        course_data["sections"]["Intake"] = "\n".join(intake_content)
        print("  ✓ Intake")
    else:
        print("  ✗ Intake not found")

    # ============ Extract Entry Requirements ============
    # Check for "Minimum Entry Requirement" section or links
    entry_found = False
    
    # Look for the heading
    for i, line in enumerate(lines):
        if "minimum entry requirement" in line.lower():
            # Check if there's an image or PDF link nearby
            entry_found = True
            # Look for actual requirement text in next lines
            req_lines = []
            for j in range(i + 1, min(i + 20, len(lines))):
                if any(kw in lines[j].lower() for kw in ["programme outline", "intake:", "duration:", "fees"]):
                    break
                if lines[j] and len(lines[j]) > 10 and "click" not in lines[j].lower():
                    req_lines.append(lines[j])
            
            if req_lines:
                course_data["sections"]["Entry Requirements"] = "\n".join(req_lines[:5])
            else:
                course_data["sections"]["Entry Requirements"] = "Please refer to the Minimum Entry Requirement document on the course page"
            break
    
    if entry_found:
        print("  ✓ Entry Requirements")
    else:
        course_data["sections"]["Entry Requirements"] = "You can find entry requirements online at the course page"
        print("  ✗ Entry Requirements (not found - using fallback)")

    # ============ Extract Campus (optional but helpful) ============
    campus_lines = extract_section_by_heading(
        lines,
        heading_keywords=["campus:"],
        stop_keywords=["intake:", "duration:", "estimated"]
    )
    
    if campus_lines:
        # Limit to first 5 lines to avoid capturing too much
        course_data["sections"]["Campus"] = "\n".join(campus_lines[:5])
        print("  ✓ Campus")

    if not course_data["sections"]:
        print("  ✗ No sections extracted")

    return course_data


def get_program_type_and_name(url):
    """Infer program type and name from URL."""
    path = urlparse(url).path.rstrip("/")
    parts = path.split("/")
    
    # Get the last part as the program name
    slug = parts[-1] if parts else "unknown"
    
    # Determine type from URL path
    path_lower = path.lower()
    if "foundation" in path_lower:
        ptype = "Foundation"
    elif "diploma" in path_lower and "advanced" not in path_lower:
        ptype = "Diploma"
    elif "bachelor" in path_lower or "bachelors-degree" in path_lower or "bachelor-degree" in path_lower:
        ptype = "Degree"
    elif "master" in path_lower:
        ptype = "Master"
    elif "phd" in path_lower or "doctor" in path_lower:
        ptype = "PhD"
    else:
        ptype = "Other"

    return ptype, slug


def format_markdown(courses_by_type):
    """Format the extracted data into Markdown."""
    lines = []
    order = ["Foundation", "Diploma", "Degree", "Master", "PhD", "Other"]

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
            
            # Fixed order of sections
            for section_title in [
                "Programme Structure",
                "Fee",
                "Duration",
                "Intake",
                "Entry Requirements",
                "Campus",
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
    parser = argparse.ArgumentParser(description="Extract TARC course info to markdown")
    parser.add_argument("input_file", help="Input file containing course URLs")
    parser.add_argument("--output", default="tarc_courses.md", help="Output markdown file")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between requests (seconds)")
    args = parser.parse_args()

    # Read URLs from file
    urls = extract_urls_from_file(args.input_file)
    print(f"Found {len(urls)} course URLs.\n")

    if not urls:
        print("No URLs found in input file")
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
        print()

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