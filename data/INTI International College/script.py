#!/usr/bin/env python3
# python .\script.py .\input.txt --output courses.md
"""
INTI Course Information Scraper
Extracts course details from INTI university website and formats them into markdown.
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
    
    # Find all <a> tags with href containing /programme/
    for link in soup.find_all('a', href=True):
        href = link['href']
        if '/programme/' in href and href not in urls:
            urls.append(href)
    
    return urls


def extract_text_from_element(element):
    """Extract and clean text from a BeautifulSoup element."""
    if not element:
        return ""
    
    # Get text and clean it up
    text = element.get_text(separator='\n', strip=True)
    # Remove excessive newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def extract_course_info(url):
    """Fetch a course page and extract relevant information."""
    print(f"Fetching: {url}")
    
    # Headers to mimic a real browser
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find the inti_long div
        inti_long = soup.find('div', class_='inti_long')
        if not inti_long:
            print(f"  Warning: No inti_long div found on {url}")
            return None
        
        # Extract course information
        course_data = {
            'url': url,
            'sections': {}
        }
        
        # Define sections to extract
        sections_map = {
            'structure': 'Programme Structure',
            'fees': 'Fees',
            'entry_requirements': 'Entry Requirements',
            'campus_intake': 'Campus Intakes',
            'career-path': 'Career Opportunities',
            'highlights': 'Additional information'
        }
        
        # Extract each section
        for class_name, section_title in sections_map.items():
            section_div = inti_long.find('div', class_=class_name) or \
                         inti_long.find('div', class_=class_name.replace('-', '_'))
            
            if section_div:
                # Find the collapse div with actual content
                content_div = section_div.find('div', class_='collapse')
                if content_div:
                    content = extract_text_from_element(content_div)
                    if content:
                        course_data['sections'][section_title] = content
        
        return course_data
        
    except requests.RequestException as e:
        print(f"  Error fetching {url}: {e}")
        return None
    except Exception as e:
        print(f"  Error parsing {url}: {e}")
        return None


def get_program_type_and_name(url):
    """Extract program type and name from URL."""
    # Extract the last part of the URL
    path = urlparse(url).path
    program_slug = path.rstrip('/').split('/')[-1]
    
    # Determine program type
    if 'certificate' in program_slug.lower():
        program_type = 'certificate'
    elif 'diploma' in program_slug.lower():
        program_type = 'diploma'
    elif 'degree' in program_slug.lower() or 'bachelor' in program_slug.lower():
        program_type = 'degree'
    elif 'foundation' in program_slug.lower():
        program_type = 'foundation'
    elif 'master' in program_slug.lower():
        program_type = 'master'
    else:
        program_type = 'other'
    
    # Format program name
    program_name = program_slug.replace('-', ' ').title()
    
    return program_type, program_slug, program_name


def format_markdown(courses_by_type):
    """Format the extracted course data into markdown."""
    markdown_lines = []
    
    # Sort program types for consistent output
    type_order = ['foundation', 'certificate', 'diploma', 'degree', 'master', 'other']
    sorted_types = sorted(
        courses_by_type.keys(), 
        key=lambda x: type_order.index(x) if x in type_order else len(type_order)
    )
    
    for program_type in sorted_types:
        # Add level 1 heading for program type
        markdown_lines.append(f"# {program_type.title()}")
        markdown_lines.append("")
        
        # Sort courses within each type
        courses = sorted(courses_by_type[program_type], key=lambda x: x['slug'])
        
        for course in courses:
            # Add level 2 heading for course name
            markdown_lines.append(f"## {course['slug']}")
            markdown_lines.append("")
            
            # ✅ Add URL section
            markdown_lines.append("### URL")
            markdown_lines.append("")
            markdown_lines.append(course['url'])
            markdown_lines.append("")
            
            # Add each section in order
            section_order = [
                'Programme Structure',
                'Fees',
                'Entry Requirements',
                'Campus Intakes',
                'Career Opportunities',
                'Additional information'
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
        description='Extract INTI course information and format into markdown'
    )
    parser.add_argument(
        'input_file',
        help='Input HTML file containing course URLs'
    )
    parser.add_argument(
        '--output',
        default='courses.md',
        help='Output markdown file (default: courses.md)'
    )
    parser.add_argument(
        '--delay',
        type=float,
        default=1.0,
        help='Delay between requests in seconds (default: 1.0)'
    )
    
    args = parser.parse_args()
    
    # Read input file
    print(f"Reading URLs from: {args.input_file}")
    try:
        with open(args.input_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
    except FileNotFoundError:
        print(f"Error: File '{args.input_file}' not found")
        return 1
    
    # Extract URLs
    urls = extract_urls_from_html(html_content)
    print(f"Found {len(urls)} unique URLs")
    
    if not urls:
        print("No URLs found in the input file")
        return 1
    
    # Extract course information
    courses_by_type = defaultdict(list)
    
    for i, url in enumerate(urls, 1):
        print(f"\nProcessing {i}/{len(urls)}")
        
        course_data = extract_course_info(url)
        
        if course_data and course_data['sections']:
            program_type, slug, name = get_program_type_and_name(url)
            
            courses_by_type[program_type].append({
                'slug': slug,
                'name': name,
                'url': course_data['url'],
                'sections': course_data['sections']
            })
        
        # Be polite to the server
        if i < len(urls):
            time.sleep(args.delay)
    
    # Format and write output
    print(f"\n\nGenerating markdown output...")
    markdown_content = format_markdown(courses_by_type)
    
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    
    print(f"Output written to: {args.output}")
    print(f"Total courses processed: {sum(len(courses) for courses in courses_by_type.values())}")
    
    return 0


if __name__ == '__main__':
    exit(main())