#!/usr/bin/env python3
"""
MCKL Course Information Scraper
Extracts course details from MCKL website and formats them into markdown.
"""

import argparse
import re
import time
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import requests
from collections import defaultdict


def extract_urls_from_file(filepath):
    """Extract all URLs from the input file, tracking categories."""
    urls_by_category = defaultdict(list)
    current_category = None
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Check if it's a category marker
                if line.startswith('#'):
                    # Extract category name (remove # and "Programs"/"Program")
                    category = line.replace('#', '').strip()
                    category = re.sub(r'^Programs?\s+', '', category, flags=re.I)
                    current_category = category
                # Check if it's a URL
                elif line and line.startswith('http'):
                    if current_category:
                        urls_by_category[current_category].append(line)
                    else:
                        urls_by_category['Other'].append(line)
        
        return urls_by_category
    except FileNotFoundError:
        print(f"Error: File '{filepath}' not found")
        return {}


def clean_text(text):
    """Clean and normalize text."""
    if not text:
        return ""
    # Remove excessive whitespace and newlines
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    return text.strip()


def extract_tab_content(soup, tab_title):
    """Extract content from a specific Elementor tab."""
    # Find all tab titles
    tab_titles = soup.find_all('div', class_='elementor-tab-title')
    
    for i, title_elem in enumerate(tab_titles):
        title = clean_text(title_elem.get_text())
        
        # Check if this is the tab we're looking for
        if re.search(tab_title, title, re.I):
            # Get the data-tab attribute
            data_tab = title_elem.get('data-tab')
            
            if data_tab:
                # Find the corresponding content div
                content_div = soup.find('div', {
                    'id': f'elementor-tab-content-{data_tab}',
                    'class': 'elementor-tab-content'
                })
                
                if not content_div:
                    # Try alternative selector
                    content_div = soup.find('div', {
                        'data-tab': data_tab,
                        'class': 'elementor-tab-content'
                    })
                
                if content_div:
                    return extract_content_from_div(content_div)
    
    return ""


def extract_content_from_div(div):
    """Extract and format content from a div."""
    content_parts = []
    
    # Extract paragraphs
    for p in div.find_all('p'):
        text = clean_text(p.get_text())
        if text:
            content_parts.append(text)
    
    # Extract lists
    for ul in div.find_all(['ul', 'ol']):
        for li in ul.find_all('li', recursive=False):
            text = clean_text(li.get_text())
            if text:
                content_parts.append(f"- {text}")
    
    # Extract tables
    for table in div.find_all('table'):
        table_content = extract_table_content(table)
        if table_content:
            content_parts.append(table_content)
    
    return '\n\n'.join(content_parts)


def extract_table_content(table):
    """Extract content from a table."""
    content = []
    
    rows = table.find_all('tr')
    for row in rows:
        cells = row.find_all(['td', 'th'])
        if cells:
            cell_texts = []
            for cell in cells:
                # Check for nested lists in cells
                nested_lists = cell.find_all(['ul', 'ol'])
                if nested_lists:
                    # Extract list items
                    items = []
                    for ul in nested_lists:
                        for li in ul.find_all('li'):
                            text = clean_text(li.get_text())
                            if text:
                                items.append(f"  - {text}")
                    
                    # Get cell header if any
                    paragraphs = cell.find_all('p')
                    if paragraphs:
                        header = clean_text(paragraphs[0].get_text())
                        if header:
                            cell_texts.append(f"**{header}**\n" + '\n'.join(items))
                        else:
                            cell_texts.append('\n'.join(items))
                    else:
                        cell_texts.append('\n'.join(items))
                else:
                    text = clean_text(cell.get_text())
                    if text:
                        cell_texts.append(text)
            
            if cell_texts:
                # If it's a multi-column table, join with |
                if len(cell_texts) > 1:
                    content.append(' | '.join(cell_texts))
                else:
                    content.append(cell_texts[0])
    
    return '\n'.join(content)


def extract_intake_info(soup):
    """Extract intake information from badges and text."""
    intake_info = []
    
    # Find badge elements
    badges = soup.find_all('span', class_='badge')
    for badge in badges:
        text = clean_text(badge.get_text())
        if text and text.lower() not in ['full time', 'part time']:
            intake_info.append(text)
    
    # Find "Next Intake" text
    for p in soup.find_all('p'):
        text = p.get_text()
        if 'Next Intake' in text or 'next intake' in text:
            # Extract the intake months/dates
            strong = p.find('strong', class_='h4')
            if strong:
                intake_text = clean_text(strong.get_text())
                if intake_text:
                    intake_info.append(f"Next Intake: {intake_text}")
            else:
                # Try to extract from the full text
                match = re.search(r'Next Intake:\s*(.+)', text, re.I)
                if match:
                    intake_text = clean_text(match.group(1))
                    if intake_text:
                        intake_info.append(f"Next Intake: {intake_text}")
    
    return '\n'.join(intake_info) if intake_info else "No intake information available"


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
        
        # Extract Programme Structure
        prog_structure = extract_tab_content(soup, 'Programme Structure|Subjects Offered|Overall Course Structure|Skill You Will Learn|Subjects Offering')
        if prog_structure:
            course_data['sections']['Programme Structure'] = prog_structure
            print(f"  ✓ Programme Structure")
        else:
            course_data['sections']['Programme Structure'] = "No information available"
            print(f"  ✗ Programme Structure - not found")
        
        # Extract Entry Requirements
        entry_req = extract_tab_content(soup, 'Entry Requirement')
        if entry_req:
            course_data['sections']['Entry Requirements'] = entry_req
            print(f"  ✓ Entry Requirements")
        else:
            course_data['sections']['Entry Requirements'] = "No information available"
            print(f"  ✗ Entry Requirements - not found")
        
        # Extract Fees
        fees = extract_tab_content(soup, 'Programme Fees|Fees')
        if fees:
            course_data['sections']['Fees'] = fees
            print(f"  ✓ Fees")
        else:
            course_data['sections']['Fees'] = "No information available"
            print(f"  ✗ Fees - not found")
        
        # Extract Intake
        intake = extract_intake_info(soup)
        course_data['sections']['Intake'] = intake
        print(f"  ✓ Intake")
        
        return course_data
        
    except requests.RequestException as e:
        print(f"  Error fetching: {e}")
        return None
    except Exception as e:
        print(f"  Error parsing: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_course_name_from_url(url):
    """Extract course name from URL."""
    path = urlparse(url).path
    course_slug = path.rstrip('/').split('/')[-1]
    return course_slug


def format_markdown(courses_by_category):
    """Format the extracted course data into markdown."""
    markdown_lines = []
    
    # Define category order and titles
    category_titles = {
        'PreU': 'Foundation',
        'Diploma': 'Diploma',
        'PD': 'Certificate',
        'Micro': 'Certificate',
        'Preparatory': 'Certificate',
        'Other': 'Other Programmes'
    }
    
    # Sort categories
    sorted_categories = sorted(courses_by_category.keys(),
                               key=lambda x: list(category_titles.keys()).index(x) 
                               if x in category_titles else 999)
    
    for category in sorted_categories:
        # Get title
        title = category_titles.get(category, category)
        
        markdown_lines.append(f"# {title}")
        markdown_lines.append("")
        
        courses = sorted(courses_by_category[category], key=lambda x: x['slug'])
        
        for course in courses:
            markdown_lines.append(f"## {course['slug']}")
            markdown_lines.append("")
            
            section_order = [
                'Programme Structure',
                'Entry Requirements',
                'Fees',
                'Intake'
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
        description='Extract MCKL course information and format into markdown'
    )
    parser.add_argument(
        'input_file',
        help='Input text file containing course URLs (one per line, with # category markers)'
    )
    parser.add_argument(
        '--output',
        default='mckl_courses.md',
        help='Output markdown file (default: mckl_courses.md)'
    )
    parser.add_argument(
        '--delay',
        type=float,
        default=2.0,
        help='Delay between requests in seconds (default: 2.0)'
    )
    
    args = parser.parse_args()
    
    print(f"Reading URLs from: {args.input_file}")
    urls_by_category = extract_urls_from_file(args.input_file)
    
    if not urls_by_category:
        print("No URLs found in the input file")
        return 1
    
    total_urls = sum(len(urls) for urls in urls_by_category.values())
    print(f"Found {total_urls} URLs in {len(urls_by_category)} categories\n")
    
    courses_by_category = defaultdict(list)
    successful = 0
    failed = 0
    current = 0
    
    for category, urls in urls_by_category.items():
        print(f"\n{'='*60}")
        print(f"Category: {category} ({len(urls)} courses)")
        print(f"{'='*60}")
        
        for url in urls:
            current += 1
            print(f"\n[{current}/{total_urls}]")
            
            course_data = extract_course_info(url)
            
            if course_data:
                slug = get_course_name_from_url(url)
                
                courses_by_category[category].append({
                    'slug': slug,
                    'sections': course_data['sections']
                })
                successful += 1
            else:
                failed += 1
            
            if current < total_urls:
                time.sleep(args.delay)
    
    print(f"\n{'='*60}")
    print(f"Successful: {successful} | Failed: {failed}")
    print(f"{'='*60}\n")
    
    if successful > 0:
        markdown_content = format_markdown(courses_by_category)
        
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        
        print(f"✓ Output written to: {args.output}\n")
        
        print(f"Courses by category:")
        for category in sorted(courses_by_category.keys()):
            count = len(courses_by_category[category])
            print(f"  {category}: {count}")
    else:
        print("No courses were successfully extracted.")
    
    return 0


if __name__ == '__main__':
    exit(main())