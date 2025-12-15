#!/usr/bin/env python3
"""
Usage:
    python3 "data/University of Wollongong/script.py" \
      "data/University of Wollongong/input.txt" \
      --output "data/University of Wollongong/Courses.md"
"""

import argparse
import re
import time
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests
from collections import defaultdict


def extract_urls_from_html(html_content):
    """Extract all course URLs from the HTML content."""
    soup = BeautifulSoup(html_content, 'html.parser')
    urls = []

    for link in soup.find_all('a', href=True):
        href = link['href']
        if '/programme/' in href and href not in urls:
            if href.startswith('http'):
                urls.append(href)

    return urls


def clean_text(text):
    """Clean and normalize text."""
    if not text:
        return ""
    # Remove excessive whitespace and newlines
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    return text.strip()

# Detect "Semester 1/2/3" or "Year 1/2/3/4"
SEM_YR_LINE = re.compile(r'^\s*(?:sem(?:ester)?|semester|year)\s*\d+\s*:?\s*$', re.I)

FIELD_HDRS = {
    'arts & humanities': 'Arts & Humanities',
    'it / computer science': 'IT / Computer Science',
    'pure sciences': 'Pure Sciences',
    'pure science': 'Pure Sciences',
    'physical sciences': 'Physical Sciences',
    'physical science': 'Physical Sciences',
}

# NOTE: capturing group ( ... ) so m.group(1) works
FIELD_LINE = re.compile(
    r'^\s*(arts\s*&\s*humanities|it\s*/\s*computer\s*science|pure\s*sciences?|physical\s*sciences?)\s*$',
    re.I
)

# Also force-bullet these labeled blocks
BULLETIFY_LABELS = {'electives', 'mpu', 'notes', 'specialisations', 'specializations', 'core', 'areas of research'}

# Lines that are ALL CAPS headers inside Specialisations (e.g., RETAIL ANALYTICS, INTERNET OF THINGS)
SPECIALISATION_HDR_RE = re.compile(r'^[A-Z0-9 /&\-\(\)]+$')

RESEARCH_HDR_RE = re.compile(
    r'^(?:RESEARCH PROPOSAL|RESEARCH|DISSERTATION|VIVA VOCE|VIVA VOLCE)\s*$',
    re.I
)

AWARD_HEADER_RE = re.compile(r'^[A-Za-z0-9].*\(\s*(?:Dual|Single)\s+Award\s+option\s*\)\s*$', re.I)
# Line that is just "(Dual Award option)" or "(Single Award option)"
PAREN_ONLY_RE   = re.compile(r'^\(\s*(?:Dual|Single)\s+Award\s+option\s*\)\s*$', re.I)

def _merge_parenthetical_headers(lines: list[str]) -> list[str]:
    """Join 'Title' + '(Dual/Single Award option)' split across two lines."""
    merged, i = [], 0
    n = len(lines)
    while i < n:
        cur = lines[i].strip()
        if i + 1 < n and PAREN_ONLY_RE.match(lines[i + 1].strip()) and cur:
            merged.append(f"{cur} {lines[i + 1].strip()}")
            i += 2
        else:
            merged.append(cur)
            i += 1
    return merged

def _should_bulletify(text: str) -> bool:
    """Only transform blocks that actually have Semester/Year or the Electives field headers."""
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if SEM_YR_LINE.match(s) or FIELD_LINE.match(s):
            return True
    return False

def _bulletify_semesters_and_fields(text: str) -> str:
    lines = _merge_parenthetical_headers((text or "").splitlines())
    out = []

    for raw in lines:
        line = (raw or "").strip()
        if not line:
            continue

        # Award-option headers (not bullets)
        if AWARD_HEADER_RE.match(line):
            out.append(f"**{line}**")
            continue

        # Semester / Year headers (not bullets)
        if SEM_YR_LINE.match(line):
            out.append(f"**{line}**")
            continue

        # Electives field headers (not bullets)
        m = FIELD_LINE.match(line)
        if m:
            key = m.group(1).lower()
            canonical = FIELD_HDRS.get(key, line)
            out.append(f"**{canonical}**")
            continue

        # Core sub-headers (not bullets)
        if RESEARCH_HDR_RE.match(line):
            out.append(f"**{line}**")
            continue

        # Specialisations: ALL-CAPS category lines → bold (not bullets)
        if SPECIALISATION_HDR_RE.match(line):
            out.append(f"**{line}**")
            continue

        # Otherwise: subject/paragraph line → bullet (strip any leading bullet chars)
        subj = re.sub(r'^\s*[-–—•·]\s*', '', line)
        out.append(f"- {subj}")

    return "\n".join(out).strip()

def _split_labeled_blocks(text: str, allowed_labels=None):
    blocks = {}
    order = []
    current_label = None
    buffer = []

    for line in (text or "").splitlines():
        stripped = line.strip()
        m = re.match(r"^\*\*(.+?)\*\*$", stripped)
        label = m.group(1).strip() if m else None

        if m and (allowed_labels is None or label.lower() in allowed_labels):
            # Start a new block for this label
            if current_label is not None:
                blocks[current_label] = "\n".join(buffer).strip()
            current_label = label
            order.append(current_label)
            buffer = []
        else:
            if current_label is not None:
                buffer.append(line)
            else:
                # Ignore text before the first allowed header
                continue

    if current_label is not None:
        blocks[current_label] = "\n".join(buffer).strip()

    return blocks, order


def _extract_duration_block(prog_text: str):
    # We still recognise Program Location as a header so we can drop it cleanly
    allowed = {s.lower() for s in ["Duration", "Program Location", "Core", "MPU", "Notes"]}
    blocks, order = _split_labeled_blocks(prog_text, allowed_labels=allowed)

    # Take Duration out (for the separate Duration section)
    duration_block = blocks.pop("Duration", "").strip() if "Duration" in blocks else ""

    # Rebuild Programme Structure WITHOUT Duration and WITHOUT Program Location
    remaining_parts = []
    for label in order:
        if label in ("Duration", "Program Location"):
            # skip both
            continue
        content = blocks.get(label, "").strip()
        if not content:
            continue
        remaining_parts.append(f"**{label}**\n\n{content}")

    new_prog = "\n".join(part for part in remaining_parts if part).strip()
    return duration_block, new_prog

def _split_fee_and_intakes(fee_intakes_text: str):
    blocks, order = _split_labeled_blocks(fee_intakes_text, allowed_labels=None)

    fee_lines: list[str] = []
    intake_lines: list[str] = []

    def is_fee_line(line: str) -> bool:
        ll = line.lower()
        return (
            "rm" in ll
            or "local students" in ll
            or "international students" in ll
            or "tuition" in ll
            or "per year" in ll
            or "per subject" in ll
        )

    for label in order:
        content = (blocks.get(label) or "").strip()
        if not content:
            continue

        label_md = f"**{label}**"
        content_lines = [ln for ln in content.splitlines() if ln.strip()]
        lower_label = label.lower()

        # -------- INTake-labeled block: split inside --------
        if "intake" in lower_label:
            intake_block: list[str] = []
            fee_block: list[str] = []

            for line in content_lines:
                if is_fee_line(line):
                    fee_block.append(line)
                else:
                    intake_block.append(line)

            # Add intake part (with label) if any
            if intake_block:
                if intake_lines and intake_lines[-1] != "":
                    intake_lines.append("")
                intake_lines.append(label_md)
                intake_lines.append("")
                intake_lines.extend(intake_block)

            # Add fee part (without repeating the "Intake dates" label) if any
            if fee_block:
                if fee_lines and fee_lines[-1] != "":
                    fee_lines.append("")
                fee_lines.extend(fee_block)

        # -------- Fee-labeled block: whole thing goes to Fee --------
        elif "fee" in lower_label:
            if fee_lines and fee_lines[-1] != "":
                fee_lines.append("")
            fee_lines.append(label_md)
            fee_lines.append("")
            fee_lines.extend(content_lines)

        # -------- All other labels (e.g. Notes) are fee-related --------
        else:
            if fee_lines and fee_lines[-1] != "":
                fee_lines.append("")
            fee_lines.append(label_md)
            fee_lines.append("")
            fee_lines.extend(content_lines)

    fee_text = "\n".join(fee_lines).strip()
    intake_text = "\n".join(intake_lines).strip()
    return fee_text, intake_text

def extract_tab_content(soup, tab_id):
    """Extract content from a specific tab."""
    tab_pane = soup.find('div', {'id': tab_id})
    if not tab_pane:
        return ""

    content_parts = []

    # Find all rows in the tab
    rows = tab_pane.find_all('div', class_='progs-row')

    for row in rows:
        col1 = row.find('div', class_='progs-col1')
        col2 = row.find('div', class_='progs-col2')

        if col1 and col2:
            label = col1.get_text(strip=True)

            # Extract content from col2
            content_html = col2

            # Process tables separately for better formatting
            tables = content_html.find_all('table')
            if tables:
                # If there are tables, format them nicely
                table_text = []
                for table in tables:
                    rows_in_table = table.find_all('tr')
                    for tr in rows_in_table:
                        cells = tr.find_all(['td', 'th'])
                        row_text = ' | '.join([clean_text(cell.get_text()) for cell in cells])
                        if row_text.strip():
                            table_text.append(row_text)

                # Get text outside tables
                for table in content_html.find_all('table'):
                    table.decompose()

                other_text = content_html.get_text(separator='\n', strip=True)

                if other_text:
                    content_parts.append(f"**{label}**\n\n{other_text}")
                if table_text:
                    content_parts.append('\n'.join(table_text))
            else:
                raw_text = content_html.get_text(separator='\n', strip=True)
                # Force bullet formatting for these blocks even if no Semester/Year/Field headers are present
                force_block = label.strip().lower() in BULLETIFY_LABELS
                content_text = _bulletify_semesters_and_fields(raw_text) if (
                            force_block or _should_bulletify(raw_text)) else raw_text
                if label and content_text:
                    content_parts.append(f"**{label}**\n\n{content_text}")

    return '\n\n'.join(content_parts)


def extract_course_info(url):
    """Fetch a course page and extract relevant information."""
    print(f"Fetching: {url}")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        response.encoding = 'utf-8'

        soup = BeautifulSoup(response.text, 'html.parser')

        course_data = {
            'url': url,
            'sections': {}
        }

        # Define sections to extract (excluding study-route)
        sections_map = {
            'prog-struc': 'Programme Structure',
            'fee-intakes': 'Fee & Intakes',
            'entry': 'Entry Requirements'
        }

        # Extract each section
        for tab_id, section_title in sections_map.items():
            content = extract_tab_content(soup, tab_id)
            if content:
                course_data['sections'][section_title] = content
                print(f"  ✓ {section_title}")
            else:
                print(f"  ✗ {section_title} - no content found")

        # --- UOW-specific post-processing: split Duration / Fee / Intakes ---
        sections = course_data['sections']

        # 1. Split Duration out of Programme Structure
        prog_text = sections.get('Programme Structure')
        if prog_text:
            duration_block, new_prog = _extract_duration_block(prog_text)
            if duration_block:
                sections['Programme Structure'] = new_prog
                sections['Duration'] = duration_block

        # 2. Split Fee & Intakes into separate Fee and Intakes sections
        fee_intakes_text = sections.get('Fee & Intakes')
        if fee_intakes_text:
            fee_block, intake_block = _split_fee_and_intakes(fee_intakes_text)
            # Only replace if we actually got something
            if fee_block:
                sections['Fee'] = fee_block
            if intake_block:
                sections['Intakes'] = intake_block
            if fee_block or intake_block:
                # Remove combined section so we don't print it twice
                sections.pop('Fee & Intakes', None)

        if not course_data['sections']:
            print(f"  Warning: No sections extracted from {url}")

        return course_data


    except requests.RequestException as e:
        print(f"  Error fetching: {e}")
        return None
    except Exception as e:
        print(f"  Error parsing: {e}")
        import traceback
        traceback.print_exc()
        return None

def normalize_program_slug(program_slug: str) -> str:
    m = re.match(r'^(.*-hons)-\d+$', program_slug, re.IGNORECASE)
    if m:
        # Keep the part up to '-hons', drop the final '-<digit>'
        return m.group(1)
    return program_slug

def get_program_type_and_name(url):
    """Extract program type and name from URL."""
    path = urlparse(url).path
    program_slug = path.rstrip('/').split('/')[-1]

    # NEW: normalise slug so that '-hons-3' style suffixes are cleaned
    program_slug = normalize_program_slug(program_slug)

    slug_lower = program_slug.lower()

    if 'certificate' in slug_lower:
        program_type = 'certificate'
    elif 'diploma' in slug_lower:
        program_type = 'diploma'
    elif 'bachelor' in slug_lower or 'degree' in slug_lower or '-hons' in slug_lower:
        program_type = 'degree'
    elif 'foundation' in slug_lower:
        program_type = 'foundation'
    elif 'master' in slug_lower:
        program_type = 'master'
    elif 'phd' in slug_lower or 'doctor' in slug_lower:
        program_type = 'phd'
    else:
        program_type = 'other'

    return program_type, program_slug


def format_markdown(courses_by_type):
    """Format the extracted course data into markdown."""
    markdown_lines = []

    type_order = ['foundation', 'certificate', 'diploma', 'degree', 'master', 'phd', 'other']
    sorted_types = sorted(courses_by_type.keys(),
                          key=lambda x: type_order.index(x) if x in type_order else len(type_order))

    for program_type in sorted_types:
        markdown_lines.append(f"# {program_type.title()}")
        markdown_lines.append("")

        courses = sorted(courses_by_type[program_type], key=lambda x: x['slug'])

        for course in courses:
            markdown_lines.append(f"## {course['slug']}")
            markdown_lines.append("")
            markdown_lines.append(f"### URL")
            markdown_lines.append("")
            markdown_lines.append(course['url'])
            markdown_lines.append("")

            section_order = [
                'Programme Structure',
                'Entry Requirements',
                'Fee',
                'Intakes',
                'Duration',
                'Fee & Intakes',  # fallback if splitting failed for some course
            ]

            for section_title in section_order:
                if section_title in course['sections']:
                    markdown_lines.append(f"### {section_title}")
                    markdown_lines.append("")
                    markdown_lines.append(course['sections'][section_title])
                    markdown_lines.append("")

            markdown_lines.append("---")
            markdown_lines.append("")

    return '\n'.join(markdown_lines)


def main():
    parser = argparse.ArgumentParser(
        description='Extract UOW Malaysia course information and format into markdown'
    )
    parser.add_argument(
        'input_file',
        help='Input HTML/text file containing course URLs'
    )
    parser.add_argument(
        '--output',
        default='courses.md',
        help='Output markdown file (default: courses.md)'
    )
    parser.add_argument(
        '--delay',
        type=float,
        default=1.5,
        help='Delay between requests in seconds (default: 1.5)'
    )

    args = parser.parse_args()

    print(f"Reading URLs from: {args.input_file}")
    try:
        with open(args.input_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except FileNotFoundError:
        print(f"Error: File '{args.input_file}' not found")
        return 1

    urls = extract_urls_from_html(html_content)
    print(f"Found {len(urls)} unique URLs\n")

    if not urls:
        print("No URLs found in the input file")
        return 1

    courses_by_type = defaultdict(list)
    successful = 0
    failed = 0

    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}]")

        course_data = extract_course_info(url)

        if course_data and course_data['sections']:
            program_type, slug = get_program_type_and_name(url)

            courses_by_type[program_type].append({
                'slug': slug,
                'url': url,
                'sections': course_data['sections']
            })
            successful += 1
        else:
            failed += 1

        if i < len(urls):
            time.sleep(args.delay)

    print(f"\n{'=' * 60}")
    print(f"Successful: {successful} | Failed: {failed}")
    print(f"{'=' * 60}\n")

    if successful > 0:
        markdown_content = format_markdown(courses_by_type)

        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        print(f"✓ Output written to: {args.output}\n")

        print(f"Programs by type:")
        for prog_type in sorted(courses_by_type.keys()):
            count = len(courses_by_type[prog_type])
            print(f"  {prog_type.title()}: {count}")
    else:
        print("No courses were successfully extracted.")

    return 0


if __name__ == '__main__':
    exit(main())