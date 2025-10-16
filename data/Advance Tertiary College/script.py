#!/usr/bin/env python3
"""
ATC2U Course Information Scraper
Extracts course details from ATC2U website and formats them into markdown.
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


def extract_fee_content(soup):
    """Extract fee information - handles both table and paragraph formats."""
    content = []
    
    # Find the heading - check both "Course Fee" and "Course Fees"
    heading, heading_type = find_heading_element(soup, 'Course Fee')
    if not heading:
        return ""
    
    # Find the next text-editor widget
    widget = find_next_text_editor_widget(heading, heading_type)
    if not widget:
        return ""
    
    # Try table first
    table = widget.find('table')
    if table:
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if cells:
                # Extract cell text and clean it
                cell_texts = [clean_text(cell.get_text()) for cell in cells]
                
                # Skip empty rows
                if not any(cell_texts):
                    continue
                
                # Check if it's a header row with bold text or colspan
                first_cell = cells[0]
                if first_cell.find('strong') or first_cell.get('colspan'):
                    # Header row - check if it's the main title or section header
                    header_text = clean_text(first_cell.get_text())
                    if header_text and 'Bachelor of Laws' in header_text:
                        # Section header
                        if content:  # Add separator before new section
                            content.append('')
                        content.append(f"**{header_text}**")
                    continue
                
                # Format data rows
                # If all cells have content, it's likely a data row
                non_empty = [t for t in cell_texts if t and t.strip() and t.strip() != ' ']
                if non_empty:
                    # Check if it's a label: value format
                    if len(non_empty) == 2 and ':' not in non_empty[0]:
                        content.append(f"{non_empty[0]}: {non_empty[1]}")
                    else:
                        # Multi-column row
                        content.append(' | '.join(non_empty))
    else:
        # Try paragraphs (Foundation style)
        for p in widget.find_all('p'):
            text = clean_text(p.get_text())
            if text:
                content.append(text)
    
    return '\n'.join(content)


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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
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
        
        # Extract Programme Structure - try accordion first, then simple list
        accordion_sections = extract_accordion_content(soup)
        if accordion_sections:
            # Join all sections with double newline
            course_data['sections']['Programme Structure'] = '\n\n'.join(accordion_sections)
            print(f"  ✓ Programme Structure ({len(accordion_sections)} subjects)")
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
                'sections': course_data['sections']
            })
            successful += 1
        else:
            failed += 1
        
        if i < len(urls):
            time.sleep(args.delay)
    
    print(f"\n{'='*60}")
    print(f"Successful: {successful} | Failed: {failed}")
    print(f"{'='*60}\n")
    
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