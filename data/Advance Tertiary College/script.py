#!/usr/bin/env python3
"""
    python3 "data/Advance Tertiary College/script.py" \
      "data/Advance Tertiary College/input.txt" \
      --output "data/Advance Tertiary College/Courses.md"

"""

import argparse
import re
import time
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests
from collections import defaultdict


def extract_urls_from_file(filepath):
    """Extract all URLs from the input file."""
    urls = []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith('#'):
                    # Check if it's a valid URL
                    if line.startswith('http'):
                        urls.append(line)
        return urls
    except FileNotFoundError:
        print(f"Error: File '{filepath}' not found")
        return []


def clean_text(text):
    """Clean and normalize text."""
    if not text:
        return ""
    # Remove excessive whitespace and newlines
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    return text.strip()


def find_heading_element(soup, heading_text):
    """Find heading element - either regular h2/h3 or inside icon-box."""
    # Try regular heading first
    heading = soup.find(['h2', 'h3'], string=re.compile(heading_text, re.I))
    if heading:
        return heading, 'regular'

    # Try icon-box title
    for icon_box_title in soup.find_all(['h2', 'h3'], class_='elementor-icon-box-title'):
        title_text = clean_text(icon_box_title.get_text())
        if re.search(heading_text, title_text, re.I):
            return icon_box_title, 'icon-box'

    return None, None


def find_next_text_editor_widget(heading_elem, heading_type='regular'):
    """Find the next text-editor widget after a heading."""
    if not heading_elem:
        return None

    # Get the parent widget container
    if heading_type == 'icon-box':
        # For icon-box, we need to go up more levels
        parent = heading_elem.find_parent('div', class_='elementor-widget')
    else:
        # Regular heading
        parent = heading_elem.find_parent('div', class_='elementor-widget')

    if not parent:
        return None

    # Find the next sibling widget
    next_sibling = parent.find_next_sibling('div', class_='elementor-widget')

    # Keep looking for text-editor widget
    while next_sibling:
        if 'elementor-widget-text-editor' in next_sibling.get('class', []):
            return next_sibling
        next_sibling = next_sibling.find_next_sibling('div', class_='elementor-widget')

    return None


def extract_simple_list_items(soup, heading_text):
    """Extract simple list items (for Subject/Programme Structure)."""
    items = []

    # Find the heading
    heading, heading_type = find_heading_element(soup, heading_text)
    if not heading:
        return items

    # Find the next text-editor widget
    widget = find_next_text_editor_widget(heading, heading_type)
    if not widget:
        return items

    # Find ul/ol elements in the widget
    lists = widget.find_all(['ul', 'ol'])
    for ul in lists:
        for li in ul.find_all('li', recursive=False):
            text = clean_text(li.get_text())
            if text:
                items.append(text)

    return items


def extract_list_items_recursive(element):
    """Recursively extract list items, handling nested lists."""
    items = []

    # Find all li elements
    for li in element.find_all('li'):
        # Skip if this li only contains another ul/ol (it's a wrapper)
        direct_children = [child for child in li.children if child.name in ['ul', 'ol']]

        # If li has list-style-type:none and only contains nested lists, skip it
        style = li.get('style', '')
        if 'list-style-type:none' in style and direct_children:
            continue

        # Get the text content
        text = clean_text(li.get_text())
        if text and text not in items:
            items.append(text)

    return items


def extract_entry_requirements(soup):
    """Extract entry requirements - handles both list and paragraph formats."""
    content = []

    # Find the heading (try both singular and plural)
    heading, heading_type = find_heading_element(soup, 'Entry Requirement')
    if not heading:
        return []

    # Find the next text-editor widget
    widget = find_next_text_editor_widget(heading, heading_type)
    if not widget:
        return []

    # Extract paragraphs first
    for p in widget.find_all('p'):
        text = clean_text(p.get_text())
        if text:
            content.append(text)

    # Extract list items (including nested ones)
    list_items = extract_list_items_recursive(widget)

    # If we have both paragraphs and list items, format nicely
    if content and list_items:
        # Add intro text first
        result = [content[0]]  # First paragraph is usually intro
        # Add list items
        result.extend([f"- {item}" for item in list_items])
        return result
    elif list_items:
        # Only list items
        return [f"- {item}" for item in list_items]
    elif content:
        # Only paragraphs (Foundation style)
        return content

    return []

def _table_to_matrix(table):
    matrix = []
    for row in table.find_all('tr'):
        cells = row.find_all(['td', 'th'])
        if not cells:
            continue
        texts = [clean_text(c.get_text()) for c in cells]
        if any(texts):
            matrix.append(texts)
    return matrix


def _format_llb_college_fees(matrix):
    header_idx = None
    for i, row in enumerate(matrix):
        if len(row) >= 3 and \
           row[1].lower().startswith('registration fee') and \
           row[2].lower().startswith('tuition fee'):
            header_idx = i
            break

    if header_idx is None:
        return None

    lines = []
    for row in matrix[header_idx + 1:]:
        if not row or not row[0]:
            continue
        year = row[0]
        if not re.match(r'Year\s*\d+', year, re.I):
            continue
        reg = row[1] if len(row) > 1 else ''
        tuition = row[2] if len(row) > 2 else ''
        parts = []
        if reg:
            parts.append(f"Registration Fee {reg}")
        if tuition:
            parts.append(f"Tuition Fee {tuition}")
        if parts:
            lines.append(f"{year}: " + ", ".join(parts))
    return lines or None


def _format_llb_university_fees(matrix):
    # 1. Find the header row that contains Year 1 / Year 2 / Year 3
    header_idx = None
    header_row = None

    for i, row in enumerate(matrix):
        if len(row) < 2:
            continue
        # look for any 'Year N' in columns AFTER the first column
        if any(re.match(r"Year\s*\d+", cell, re.I) for cell in row[1:]):
            header_idx = i
            header_row = row
            break

    if header_idx is None or header_row is None:
        return None  # cannot detect a year header → let generic handler deal with it

    # 2. Work out which columns correspond to which years
    year_cols = []
    for idx, cell in enumerate(header_row):
        if re.match(r"Year\s*\d+", cell, re.I):
            year_cols.append((idx, clean_text(cell)))

    if not year_cols:
        return None

    # 3. Collect pieces for each year
    year_parts = {label: [] for (_, label) in year_cols}

    for row in matrix[header_idx + 1:]:
        if not row or len(row) == 0:
            continue

        label = clean_text(row[0]).rstrip(':')
        if not label:
            continue

        for col_idx, year_label in year_cols:
            if col_idx >= len(row):
                continue
            amount = clean_text(row[col_idx])
            if not amount or amount in ('-', '—'):
                continue
            year_parts[year_label].append(f"{label} {amount}")

    # 4. Turn into markdown lines in Year 1, Year 2, Year 3 order
    def year_sort_key(y_label: str) -> int:
        m = re.search(r"\d+", y_label)
        return int(m.group(0)) if m else 999

    lines = []
    for year_label in sorted(year_parts.keys(), key=year_sort_key):
        parts = year_parts[year_label]
        if parts:
            lines.append(f"{year_label}: " + ", ".join(parts))

    return lines or None

def extract_fee_content(soup):
    """Extract fee information - handles both table and paragraph formats."""
    content = []

    # Find the heading - check both "Course Fee" and "Course Fees"
    heading, heading_type = find_heading_element(soup, 'Course Fee')
    if not heading:
        return ""

    # -------- collect ALL text-editor widgets after the Course Fee heading --------
    parent_widget = heading.find_parent('div', class_='elementor-widget')
    widgets = []

    if parent_widget is not None:
        sib = parent_widget.find_next_sibling('div', class_='elementor-widget')
        while sib:
            classes = sib.get('class', [])

            # All fee tables on ATC pages are inside text-editor widgets
            if 'elementor-widget-text-editor' in classes:
                widgets.append(sib)
                sib = sib.find_next_sibling('div', class_='elementor-widget')
                continue

            # Stop when we hit another heading widget – means new section
            if 'elementor-widget-heading' in classes:
                break

            sib = sib.find_next_sibling('div', class_='elementor-widget')

    # Fallback: keep old behaviour if we didn't find anything
    if not widgets:
        widget = find_next_text_editor_widget(heading, heading_type)
        if not widget:
            return ""
        widgets = [widget]

    # -------- process all tables / paragraphs inside those widgets --------
    for widget in widgets:
        tables = widget.find_all('table')

        if tables:
            for table in tables:
                matrix = _table_to_matrix(table)
                if not matrix:
                    continue

                first_cell = matrix[0][0] if matrix and matrix[0] else ""

                # ---------- SPECIAL CASE: ATC LLB tables ----------

                # 1) College fees only  (already working – keep behaviour, but don't re-parse)
                if 'Bachelor of Laws, University of London (College fees only)' in first_cell:
                    if content:
                        content.append("")
                    content.append("**Bachelor of Laws, University of London (College fees only)**")
                    lines = _format_llb_college_fees(matrix[1:])
                    if lines:
                        content.extend(lines)

                # 2) University of London fees (Application / Registration / Module / Exam / Total)
                #    Here we pass the FULL matrix so the function can find the Year 1/2/3 header row.
                if 'Bachelor of Laws, University of London' in first_cell:
                    llb_lines = _format_llb_university_fees(matrix)
                    if llb_lines:
                        if content:
                            content.append("")
                        content.append("**Bachelor of Laws, University of London**")
                        content.extend(llb_lines)
                        # Table fully handled, go to next table
                        continue
                    # If parsing failed (no Year 1/2/3 header), fall through to generic handler below

                # ---------- Generic fallback for all other tables ----------
                for row in matrix:
                    cell_texts = row
                    first_text = cell_texts[0] if cell_texts else ""

                    # "Year 1" etc as standalone row
                    if re.match(r'Year\s*\d+', first_text, re.I) and all(
                        not t for t in cell_texts[1:]
                    ):
                        if content:
                            content.append("")
                        content.append(f"**{first_text}**")
                        continue

                    non_empty = [t for t in cell_texts if t and t.strip()]
                    if not non_empty:
                        continue

                    # Label | value pattern (two non-empty columns)
                    if len(non_empty) == 2 and ':' not in non_empty[0]:
                        content.append(f"{non_empty[0]}: {non_empty[1]}")
                    else:
                        # Multi-column row
                        content.append(" | ".join(non_empty))
        else:
            # Fallback: plain paragraphs (for pages without tables)
            for p in widget.find_all('p'):
                text = clean_text(p.get_text())
                if text:
                    content.append(text)

    return "\n".join(content)

# --- Helper to bold "Year 1", "Year 2", etc. ---
def bold_year_headers_in_html(soup):
    year_headers = []
    for h in soup.find_all(['h2', 'h3']):
        text = clean_text(h.get_text())
        if re.match(r'Year\s*\d+', text, re.I):
            year_headers.append(f"**{text}**")
    return year_headers


def _format_subject_block(title: str, paragraphs: list[str]) -> str:
    """
    Format a single subject block.

    only keep the subject title for Programme Structure, and drop the long description so that answers stay short.
    """
    title = clean_text(title)
    if not title:
        return ""
    return f"- {title}\n"


def extract_programme_structure(soup) -> str:
    """
    Return Programme Structure as Markdown where ONLY subject titles are bulleted.
    If 'Year 1', 'Year 2', etc. are present, insert them inline before their subjects.
    """
    output_blocks = []

    # Detect year headers (in order of appearance)
    year_headers = [h for h in soup.find_all(['h2', 'h3']) if re.match(r'Year\s*\d+', clean_text(h.get_text()), re.I)]
    year_positions = {h: clean_text(h.get_text()) for h in year_headers}

    # Find all accordions (subjects)
    accordions = soup.find_all('details', class_='e-n-accordion-item')

    for acc in accordions:
        # Before each subject, check if a Year header appears before it
        prev = acc.find_previous(['h2', 'h3'])
        if prev in year_positions:
            # Only insert if not already added
            header_text = year_positions.pop(prev, None)
            if header_text:
                output_blocks.append(f"**{header_text}**\n")

        title_elem = acc.find('div', class_='e-n-accordion-item-title-text')
        content_div = acc.find('div', role='region')
        if not title_elem or not content_div:
            continue

        title = clean_text(title_elem.get_text())
        paras = []

        # Description paragraphs
        for p in content_div.find_all('p'):
            txt = clean_text(p.get_text())
            if txt:
                paras.append(txt)

        # Fallback: list items as description
        if not paras:
            for li in content_div.find_all('li'):
                txt = clean_text(li.get_text())
                if txt:
                    paras.append(txt)

        if title:
            output_blocks.append(_format_subject_block(title, paras))

    # If no accordions found, fallback to list
    if not output_blocks:
        subject_items = extract_simple_list_items(soup, r'(Subject|Programme Structure)')
        if subject_items:
            return '\n'.join(f"- {item}" for item in subject_items)

    return '\n'.join(output_blocks).strip()


def extract_accordion_content(soup):
    """Extract content from accordion/nested accordion widgets."""
    sections = []

    # Find accordion items
    accordions = soup.find_all('details', class_='e-n-accordion-item')

    for accordion in accordions:
        # Get title
        title_elem = accordion.find('div', class_='e-n-accordion-item-title-text')
        if not title_elem:
            continue

        title = clean_text(title_elem.get_text())

        # Get content
        content_div = accordion.find('div', role='region')
        if content_div:
            content_parts = []

            # Extract all paragraphs
            for p in content_div.find_all('p'):
                text = clean_text(p.get_text())
                if text:
                    content_parts.append(text)

            if content_parts:
                # Format as: Title on one line, content below
                section_text = f"{title}\n" + '\n'.join(content_parts)
                sections.append(section_text)

    return sections


def extract_info_boxes(soup):
    """Extract information from icon boxes (intake, duration, campus)."""
    info = {}

    icon_boxes = soup.find_all('div', class_='elementor-icon-box-wrapper')

    for box in icon_boxes:
        title_elem = box.find('h3', class_='elementor-icon-box-title')
        desc_elem = box.find('p', class_='elementor-icon-box-description')

        if title_elem and desc_elem:
            title = clean_text(title_elem.get_text())

            # Extract all list items if present
            items = []
            for li in desc_elem.find_all('li'):
                text = clean_text(li.get_text())
                if text:
                    items.append(text)

            # If no list items, get the text directly
            if not items:
                desc = clean_text(desc_elem.get_text())
                if desc:
                    items = [desc]

            if title and items:
                info[title] = items

    return info


def extract_course_info(url):
    """Fetch a course page and extract relevant information."""
    print(f"Fetching: {url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
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

        # Extract icon box information (Intake, Duration, Campus)
        info_boxes = extract_info_boxes(soup)

        # Extract Programme Structure (subjects bulleted; descriptions plain)
        prog_md = extract_programme_structure(soup)
        if prog_md:
            course_data['sections']['Programme Structure'] = prog_md
            # Optional: show count of subjects (lines starting with "- ")
            subj_count = sum(1 for line in prog_md.splitlines() if line.strip().startswith("- "))
            print(f"  ✓ Programme Structure ({subj_count} subjects)")

        else:
            # Try simple list (look for "Subject" heading)
            subject_items = extract_simple_list_items(soup, 'Subject|Programme Structure')
            if subject_items:
                # Format as simple list
                course_data['sections']['Programme Structure'] = '\n'.join(subject_items)
                print(f"  ✓ Programme Structure ({len(subject_items)} subjects)")

        # Extract Entry Requirements
        entry_content = extract_entry_requirements(soup)
        if entry_content:
            course_data['sections']['Entry Requirements'] = '\n'.join(entry_content)
            print(f"  ✓ Entry Requirements")

        # Extract Fees
        fee_content = extract_fee_content(soup)
        if fee_content:
            course_data['sections']['Fee'] = fee_content
            print(f"  ✓ Fee")

        # Add info from icon boxes
        if 'Intake' in info_boxes:
            intakes = '\n'.join(info_boxes['Intake'])
            course_data['sections']['Intakes'] = intakes
            print(f"  ✓ Intakes")

        if 'Duration' in info_boxes:
            duration = '\n'.join(info_boxes['Duration'])
            course_data['sections']['Duration'] = duration
            print(f"  ✓ Duration")

        if 'Campus' in info_boxes:
            campus = '\n'.join(info_boxes['Campus'])
            course_data['sections']['Campus'] = campus
            print(f"  ✓ Campus")

        return course_data

    except requests.RequestException as e:
        print(f"  Error fetching: {e}")
        return None
    except Exception as e:
        print(f"  Error parsing: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_program_type_and_name(url):
    """Extract program type and name from URL."""
    path = urlparse(url).path
    program_slug = path.rstrip('/').split('/')[-1]

    slug_lower = program_slug.lower()

    if 'foundation' in slug_lower:
        program_type = 'foundation'
    elif 'certificate' in slug_lower or 'clp' in slug_lower:
        program_type = 'certificate'
    elif 'bachelor' in slug_lower or 'degree' in slug_lower or 'llb' in slug_lower:
        program_type = 'degree'
    else:
        program_type = 'other'

    return program_type, program_slug


def format_markdown(courses_by_type):
    """Format the extracted course data into markdown."""
    markdown_lines = []

    type_order = ['foundation', 'degree', 'certificate', 'other']
    type_titles = {
        'foundation': 'Foundation',
        'degree': 'Degree',
        'certificate': 'Certificate',
        'other': 'Other'
    }

    sorted_types = [t for t in type_order if t in courses_by_type]

    for program_type in sorted_types:
        markdown_lines.append(f"# {type_titles[program_type]}")
        markdown_lines.append("")

        courses = sorted(courses_by_type[program_type], key=lambda x: x['slug'])

        for course in courses:
            markdown_lines.append(f"## {course['slug']}")
            markdown_lines.append("")

            markdown_lines.append("### URL")
            markdown_lines.append("")
            markdown_lines.append(course['url'])
            markdown_lines.append("")

            section_order = [
                'Programme Structure',
                'Entry Requirements',
                'Fee',
                'Intakes',
                'Duration',
                'Campus'
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
        description='Extract ATC2U course information and format into markdown'
    )
    parser.add_argument(
        'input_file',
        help='Input text file containing course URLs (one per line)'
    )
    parser.add_argument(
        '--output',
        default='courses.md',
        help='Output markdown file (default: courses.md)'
    )
    parser.add_argument(
        '--delay',
        type=float,
        default=2.0,
        help='Delay between requests in seconds (default: 2.0)'
    )

    args = parser.parse_args()

    print(f"Reading URLs from: {args.input_file}")
    urls = extract_urls_from_file(args.input_file)

    if not urls:
        print("No URLs found in the input file")
        return 1

    print(f"Found {len(urls)} URLs\n")

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
                'url': course_data['url'],
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