#!/usr/bin/env python3
"""
UOW Malaysia Course Information Scraper
Extracts course details from UOW Malaysia website and formats them into markdown.
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
                # No tables, just get all text
                content_text = content_html.get_text(separator='\n', strip=True)
                if label and content_text:
                    content_parts.append(f"**{label}**\n\n{content_text}")
    
    return '\n\n'.join(content_parts)


def extract_course_info(url):
    """Fetch a course page and extract relevant information."""
    print(f"Fetching: {url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
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


def get_program_type_and_name(url):
    """Extract program type and name from URL."""
    path = urlparse(url).path
    program_slug = path.rstrip('/').split('/')[-1]
    
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
            
            section_order = [
                'Programme Structure',
                'Fee & Intakes',
                'Entry Requirements'
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