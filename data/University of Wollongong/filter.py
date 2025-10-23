#!/usr/bin/env python3
"""
Filter <a> tags from HTML based on URL list (url.txt)
Keeps only <a ... href="...">...</a> blocks where href matches URL in url.txt
"""

import re

def read_urls(url_file):
    """Read and normalize URLs from url.txt"""
    urls = set()
    with open(url_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("http"):
                urls.add(line.rstrip("/"))  # remove trailing slash for consistency
    return urls


def extract_a_tags(html):
    """Extract all <a>...</a> blocks that contain an href attribute"""
    return re.findall(r"<a\b[^>]*href=\"[^\"]+\"[^>]*>.*?</a>", html, flags=re.DOTALL)


def extract_href(a_tag):
    """Extract href URL from <a> tag"""
    m = re.search(r'href="([^"]+)"', a_tag)
    return m.group(1).rstrip("/") if m else None


def filter_links(input_html_path, url_txt_path, output_html_path):
    """Filter <a> tags based on URLs in url.txt"""
    with open(input_html_path, "r", encoding="utf-8") as f:
        html = f.read()

    urls = read_urls(url_txt_path)
    a_tags = extract_a_tags(html)

    filtered = []
    for a_tag in a_tags:
        href = extract_href(a_tag)
        if href and href in urls:
            filtered.append(a_tag)

    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write("\n".join(filtered))

    print(f"✅ Found {len(filtered)} matching <a> tags out of {len(a_tags)} total.")
    print(f"💾 Saved to {output_html_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Filter <a> tags by URL list")
    parser.add_argument("input_html", help="Path to input HTML file (e.g., input.txt)")
    parser.add_argument("url_list", help="Path to URL list file (e.g., url.txt)")
    parser.add_argument("--output", default="filtered_links.html", help="Output file name")
    args = parser.parse_args()

    filter_links(args.input_html, args.url_list, args.output)
