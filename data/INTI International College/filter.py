#!/usr/bin/env python3
"""
Filter INTI programme HTML list based on existing URLs.
Keeps only <li> blocks whose <a href="..."> link exists in url.txt.
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


def extract_li_blocks(html_content):
    """Extract all <li>...</li> blocks"""
    return re.findall(r"<li\b.*?</li>", html_content, flags=re.DOTALL)


def extract_href(li_block):
    """Extract the first href from <a href="...">"""
    match = re.search(r'href="([^"]+)"', li_block)
    if match:
        return match.group(1).rstrip("/")
    return None


def filter_html(input_html_path, url_txt_path, output_html_path):
    """Filter the HTML file based on URL list"""
    with open(input_html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    urls = read_urls(url_txt_path)
    li_blocks = extract_li_blocks(html_content)

    filtered = []
    for li in li_blocks:
        href = extract_href(li)
        if href and href in urls:
            filtered.append(li)

    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write("<ul class='products columns-4'>\n")
        f.write("\n".join(filtered))
        f.write("\n</ul>")

    print(f"✅ Filtered {len(filtered)} out of {len(li_blocks)} programmes saved to {output_html_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Filter INTI programme HTML list using URLs.")
    parser.add_argument("input_html", help="Path to input HTML file (e.g., input.txt)")
    parser.add_argument("url_list", help="Path to URL list file (e.g., url.txt)")
    parser.add_argument("--output", default="filtered.html", help="Output filtered HTML file")
    args = parser.parse_args()

    filter_html(args.input_html, args.url_list, args.output)
