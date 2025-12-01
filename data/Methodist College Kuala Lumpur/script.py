#!/usr/bin/env python3
"""
MCKL Course Information Scraper
Usage:
    python3 "data/Methodist College Kuala Lumpur/script.py" \
      "data/Methodist College Kuala Lumpur/input.txt" \
      --output "data/Methodist College Kuala Lumpur/Courses.md"

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
    tab_titles = soup.find_all('div', class_='elementor-tab-title')

    for title_elem in tab_titles:
        title = clean_text(title_elem.get_text())

        if re.search(tab_title, title, re.I):
            data_tab = title_elem.get('data-tab')
            if data_tab:
                content_div = (soup.find('div', {
                    'id': f'elementor-tab-content-{data_tab}',
                    'class': 'elementor-tab-content'
                }) or soup.find('div', {
                    'data-tab': data_tab,
                    'class': 'elementor-tab-content'
                }))

                if content_div:
                    is_programme = re.search(
                        r'programme structure|subjects offered|overall course structure|skill you will learn|subjects offering|course outline',
                        title, re.I
                    )
                    # Use the specialised extractor for programme tabs
                    if is_programme:
                        # Choose a sensible default section label based on the tab title
                        default_label = 'Programme Structure'
                        if re.search(r'subjects?\s+offering|subjects?\s+offered', title, re.I):
                            default_label = 'Subjects Offering'
                        elif re.search(r'skill\s+you\s+will\s+learn', title, re.I):
                            default_label = 'Skills You Will Learn'
                        return extract_programme_structure(content_div, default_title=default_label)
                        # Fallback to generic extractor for other tabs
                    return extract_content_from_div(content_div, context='general')
    return ""

def normalize_heading(title: str) -> str:
    """Clean and normalize headings and map common synonyms."""
    if not title:
        return ""

    t = re.sub(r'\s+', ' ', title).strip()
    if re.fullmatch(r'\(?\s*new\s*\)?', t, flags=re.I):
        return ""

    # strip leading/trailing asterisks and trailing colon
    t = re.sub(r'^\*+|\*+$', '', t).strip()
    t = re.sub(r'[:：]\s*$', '', t)

    # drop pure campus markers "(KL)" "(PG)" used alone as a 'heading'
    if re.fullmatch(r'\(?\s*(KL|PG)\s*\)?', t, flags=re.I):
        return ""

    # remove trailing campus markers from real headings
    t = re.sub(r'\s*\((?:KL|PG)\)\s*$', '', t, flags=re.I)
    t = re.sub(r'\s*\^\s*(?:KL|PG)\s*$', '', t, flags=re.I)

    tl = t.lower()

    # map synonyms → canonical
    if 'mata pelajaran pengajian umum' in tl or re.search(r'\bmpu\b', tl):
        return 'Compulsory Subjects'
    if re.search(r'compulsory\s+subjects', tl):
        return 'Compulsory Subjects'
    if re.search(r'\bdiscipline\s*core\b', tl):
        return 'Discipline Core'
    if re.fullmatch(r'core', tl):
        return 'Core'
    if re.search(r'speciali[sz]ation', tl):
        return 'Specialisation'
    if re.search(r'\bolna\b', tl):
        return 'Online Literacy And Numeracy Assessment (OLNA)'
    if re.fullmatch(r'list\s*a', tl):
        return 'List A'
    if re.fullmatch(r'list\s*b', tl):
        return 'List B'

    return t


# --- MCKL-specific helpers (safe, no side-effects for other sites) ---

def _is_noise_heading(s: str) -> bool:
    """Headings that are actually disclaimers/notes/markers, not real section titles."""
    if not s:
        return False
    t = s.strip().lower()
    return (
        'uses sas content and software' in t or
        t in {'note', 'notes', 'industrial training', 'capstone project'} or
        re.fullmatch(r'\(?\s*(kl|pg)\s*\)?', t) is not None
    )

def _fix_mpu_inline_items(items):
    out = []
    for line in items:
        raw = line.lstrip('- ').strip()
        # split known inline notes
        m = re.match(r'^(Bahasa Kebangsaan A)(.*)$', raw, re.I)
        n = re.match(r'^(Bahasa Melayu Komunikasi 1)(.*)$', raw, re.I)
        if m:
            head, tail = m.group(1).strip(), m.group(2).strip()
            out.append(f"- {head}")
            if tail:
                # Normalize missing space case "...AFor local students..."
                tail = re.sub(r'^\s*for\b', 'For', tail, flags=re.I)
                out.append(f"  - {tail}")
            continue
        if n:
            head, tail = n.group(1).strip(), n.group(2).strip()
            out.append(f"- {head}")
            if tail:
                tail = re.sub(r'^\s*for\b', 'For', tail, flags=re.I)
                out.append(f"  - {tail}")
            continue

        # Dedup bare sub-lines that sometimes appear as separate bullets
        if re.match(r'^For (local|international) students', raw, re.I):
            # If previous line is a parent item, append as sub-bullet once
            if out and not out[-1].startswith('  - '):
                out.append(f"  - {raw}")
            # else: already added under the parent
            continue

        out.append(line)
    return out

def _repair_sections_towards_expected(sections):
    titles = [t for _, t, _ in sections]
    have_spec = any(re.search(r'\bspeciali[sz]ation\b', t, re.I) for t in titles)

    repaired = []
    for order, title, items in sections:
        if not items and re.search(r'industrial training', title, re.I):
            # Ignore empty Industrial Training labels
            continue

        if (not have_spec
            and re.search(r'industrial training', title, re.I)
            and len(items) >= 3
            and sum(1 for x in items if re.search(r'[A-Za-z]', x)) >= 3):
            # Likely mis-grouped; promote to Specialisation
            title = 'Specialisation'

        repaired.append((order, title, items))
    return repaired



def extract_programme_structure(div, default_title='Programme Structure'):
    content_parts = []
    seen_bullets = set()
    intro_paras = []

    def norm(s: str) -> str:
        return re.sub(r'\s+', ' ', s or '').strip().lower()

    def text_of(el) -> str:
        return clean_text(el.get_text()) if el else ''

    # ---- STEP 4 support: know which section we're in when collecting bullets
    current_section_title = None

    def collect_items_from_list(ul_or_ol):
        items = []
        for li in ul_or_ol.find_all('li'):
            t = text_of(li)
            if not t:
                continue
            # strip trailing footnote asterisks like 'Statistics**'
            t = re.sub(r'\*+$', '', t).strip()
            k = norm(t)
            if k in seen_bullets or not t:
                continue
            seen_bullets.add(k)
            items.append(f"- {t}")

        # STEP 4: If this is MPU/Compulsory Subjects, split glued “For local/…”
        if current_section_title and re.search(r'(compulsory subjects|\bmpu\b)', current_section_title, re.I):
            items = _fix_mpu_inline_items(items)
        return items

    # Keep 1–3 opening descriptive paragraphs (buffer them; decide later where to render)
    kept = 0
    for p in div.find_all('p'):
        t = text_of(p)
        if re.search(
                r'students\s+are\s+required|select a combination|will need to select|must choose|'
                r'comprises|consists|module\s*\d|lead to the award',
                t, re.I
        ):
            intro_paras.append(t)
            kept += 1
            if kept >= 3:
                break

    sections = []
    order_counter = 0

    preface_items = []
    before_first_heading = True
    for node in div.descendants:
        if not getattr(node, "name", None):
            continue
        # stop when we hit the first "heading-like" element
        if before_first_heading and (
                node.name in ("h1", "h2", "h3", "h4", "h5", "h6")
                or (node.name in ("strong", "b") and not node.find_parent("li"))
        ):
            before_first_heading = False

        if before_first_heading and node.name in ("ul", "ol"):
            # collect these list items under the default section title
            current_section_title = default_title  # reuse MPU fix logic safely
            preface_items.extend(collect_items_from_list(node))

    if preface_items:
        sections.append((order_counter, default_title, preface_items))
        order_counter += 1

    # ---------- 1) Column-based extraction (Elementor/Bootstrap/Gutenberg grids) ----------
    COL_CLASS_RE = re.compile(
        r'(elementor-column|elementor-widget-wrap|col-(?:xs|sm|md|lg|xl|xxl)-\d+|wp-block-columns|wp-block-column)'
    )
    columns = [c for c in div.find_all('div', class_=COL_CLASS_RE)]
    for col in columns:
        # heading candidates inside the column
        head = None
        for cand in col.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'div', 'span'], recursive=True):
            # Prefer real heading tags
            if cand.name in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                ht = normalize_heading(text_of(cand))
                if ht and not _is_noise_heading(ht):
                    head = cand
                    break

            # Fallback: bold/strong that is NOT inside a list item
            strong = cand.find(['strong', 'b'])
            if strong and not strong.find_parent('li'):
                ht = normalize_heading(text_of(strong))
                # ignore tiny badges like "(New)"
                if ht and not _is_noise_heading(ht) and not re.fullmatch(r'\(?\s*new\s*\)?', ht, re.I):
                    head = cand
                    break

        # list within the same column
        ul_or_ol = None
        if head:
            ul_or_ol = head.find_next(lambda t: hasattr(t, 'name') and t.name in ('ul','ol'))
            # ensure the list belongs to this column
            if ul_or_ol and col not in ul_or_ol.parents:
                ul_or_ol = None
        if not ul_or_ol:
            ul_or_ol = col.find(['ul','ol'])
        if not ul_or_ol:
            continue

        title = normalize_heading(text_of(head)) if head else ""
        if not title:
            order_counter += 1
            title = f"Section {order_counter}"

        # STEP 4: set the section we are entering before collecting items
        current_section_title = title
        items = collect_items_from_list(ul_or_ol)
        if items:
            sections.append((order_counter, title, items))
            order_counter += 1

    # ---------- 2) Generic: any heading immediately followed by a list ----------
    for node in div.find_all(['h1','h2','h3','h4','h5','h6','p','div','span'], recursive=True):
        # consider bold-within-paragraph headings
        title = ""
        if node.name in ('h1','h2','h3','h4','h5','h6'):
            title = normalize_heading(text_of(node))
        else:
            # Only consider bold text that is not inside a list item
            strong = node.find(['strong', 'b'])
            if strong and not strong.find_parent('li'):
                cand_title = normalize_heading(text_of(strong))
                if not re.fullmatch(r'\(?\s*new\s*\)?', cand_title, re.I):
                    title = cand_title

        if not title or _is_noise_heading(title):
            continue

        nxt = node.find_next(lambda t: hasattr(t, 'name') and t.name in ('ul','ol'))
        if not nxt or div not in nxt.parents:
            continue

        # STEP 4
        current_section_title = title
        items = collect_items_from_list(nxt)
        if not items:
            continue

        # avoid duplicates if already captured from column scan
        if any(normalize_heading(t2) == title for _, t2, _ in sections):
            continue

        order_counter += 1
        sections.append((order_counter, title, items))

    # ---------- 2b) Augment with table columns if present (adds any missing Core/Discipline Core/Specialisation) ----------
    def _augment_sections_with_table():
        have_titles = set(normalize_heading(t) for _, t, _ in sections)
        target_titles = {'Core', 'Discipline Core', 'Specialisation'}
        need_titles = {t for t in target_titles if t not in have_titles}
        if not need_titles:
            return

        for table in div.find_all('table'):
            rows = table.find_all('tr')
            if len(rows) < 2:
                continue

            headers = [normalize_heading(text_of(th)) for th in rows[0].find_all(['th', 'td'])]
            headers = [h for h in headers if h]
            if not headers:
                continue

            # per-column de-dup (local to each column)
            col_seen = [set() for _ in headers]
            col_bullets = [[] for _ in headers]

            def collect_cell_items(cell, j):
                items = []

                # 1) normal lists
                ul = cell.find(['ul', 'ol'])
                if ul:
                    for li in ul.find_all('li'):
                        t = re.sub(r'\*+$', '', text_of(li)).strip()
                        k = re.sub(r'\s+', ' ', t).strip().lower()
                        if t and k not in col_seen[j]:
                            col_seen[j].add(k)
                            items.append(f"- {t}")

                # 2) EXTRA: bold labels outside lists (Industrial Training / Capstone Project)
                for strong in cell.find_all(['strong', 'b']):
                    if strong.find_parent(['ul', 'ol', 'li']):
                        continue
                    label = clean_text(strong.get_text())
                    if re.fullmatch(r'(Industrial Training|Capstone Project)', label, re.I):
                        k = re.sub(r'\s+', ' ', label).strip().lower()
                        if k not in col_seen[j]:
                            col_seen[j].add(k)
                            items.append(f"- {label}")

                return items

            for r in rows[1:]:
                cells = r.find_all(['td', 'th'])

                # Skip table-wide notes (single cell spanning all columns)
                if len(cells) == 1:
                    try:
                        if int(cells[0].get('colspan', '1') or 1) >= len(headers):
                            continue
                    except Exception:
                        pass

                for j, cell in enumerate(cells[:len(headers)]):
                    col_bullets[j].extend(collect_cell_items(cell, j))

            added_any = False
            for j, h in enumerate(headers):
                if h not in target_titles or h in have_titles:
                    continue
                items = [li for li in col_bullets[j] if li.strip('- ').strip()]
                if items:
                    sections.append((order_counter + j + 1, h, items))
                    added_any = True
            if added_any:
                break

    _augment_sections_with_table()

    # ---------- 2c) Reconcile with authoritative 3-column table (override + correct order) ----------
    def _reconcile_with_threecol_table():
        nonlocal current_section_title
        target_order = ['Core', 'Discipline Core', 'Specialisation']

        for table in div.find_all('table'):
            rows = table.find_all('tr')
            if len(rows) < 2:
                continue

            headers = [normalize_heading(text_of(th)) for th in rows[0].find_all(['th', 'td'])]
            headers = [h for h in headers if h]
            if not all(t in headers for t in target_order):
                continue

            col_seen = [set() for _ in headers]
            col_bullets = [[] for _ in headers]

            def collect_cell_items(cell, j):
                items = []

                # 1) normal lists
                ul = cell.find(['ul', 'ol'])
                if ul:
                    for li in ul.find_all('li'):
                        t = re.sub(r'\*+$', '', text_of(li)).strip()
                        k = re.sub(r'\s+', ' ', t).strip().lower()
                        if t and k not in col_seen[j]:
                            col_seen[j].add(k)
                            items.append(f"- {t}")

                # 2) EXTRA: bold labels outside lists (Industrial Training / Capstone Project)
                for strong in cell.find_all(['strong', 'b']):
                    if strong.find_parent(['ul', 'ol', 'li']):
                        continue
                    label = clean_text(strong.get_text())
                    if re.fullmatch(r'(Industrial Training|Capstone Project)', label, re.I):
                        k = re.sub(r'\s+', ' ', label).strip().lower()
                        if k not in col_seen[j]:
                            col_seen[j].add(k)
                            items.append(f"- {label}")

                return items

            for r in rows[1:]:
                cells = r.find_all(['td', 'th'])

                # Skip table-wide note rows
                if len(cells) == 1:
                    try:
                        if int(cells[0].get('colspan', '1') or 1) >= len(headers):
                            continue
                    except Exception:
                        pass

                for j, cell in enumerate(cells[:len(headers)]):
                    current_section_title = headers[j]
                    col_bullets[j].extend(collect_cell_items(cell, j))

            table_map = {
                headers[j]: [li for li in col_bullets[j] if li.strip('- ').strip()]
                for j in range(len(headers))
            }

            # Rebuild only the three main sections in the right order
            keep = [s for s in sections if normalize_heading(s[1]) not in set(target_order)]
            for t in target_order:
                items = table_map.get(t, [])
                if items:
                    keep.append((order_counter + len(keep) + 1, t, items))
            sections[:] = keep
            break

    _reconcile_with_threecol_table()

    # ---------- 3) Table fallback (header row → section titles, cells → items) ----------
    if not sections:
        for table in div.find_all('table'):
            rows = table.find_all('tr')
            if len(rows) < 2:
                continue
            headers = []
            for th in rows[0].find_all(['th','td']):
                h = normalize_heading(text_of(th))
                if h and not _is_noise_heading(h):
                    headers.append(h)

            if not headers or len(headers) < 2:
                continue

            col_bullets = [[] for _ in headers]
            for r in rows[1:]:
                cells = r.find_all(['td','th'])
                for j, cell in enumerate(cells[:len(headers)]):
                    # lists first
                    ul = cell.find(['ul','ol'])
                    if ul:
                        # STEP 4: apply per-header MPU fix by collecting with context
                        current_section_title = headers[j]
                        col_bullets[j].extend(collect_items_from_list(ul))
                        continue
                    # plain text fallback (split by <br> or paragraphs)
                    texts = []
                    for piece in cell.stripped_strings:
                        piece = re.sub(r'\*+$', '', piece).strip()
                        if piece:
                            texts.append(piece)
                    if texts:
                        merged = ' '.join(texts)
                        k = norm(merged)
                        if k and k not in seen_bullets:
                            seen_bullets.add(k)
                            col_bullets[j].append(f"- {merged}")

            added_any = False
            for j, h in enumerate(headers):
                items = [li for li in col_bullets[j] if li.strip('- ').strip()]
                # STEP 4 (again): if this header is MPU/Compulsory, fix any glued items
                if items and re.search(r'(compulsory subjects|\bmpu\b)', h, re.I):
                    items = _fix_mpu_inline_items(items)
                if items:
                    sections.append((j, h, items))
                    added_any = True
            if added_any:
                break

    # ---------- 3b) List-only fallback (no headings; just bullets present) ----------
    if not sections:
        all_items = []
        local_seen = set()
        for ul in div.find_all(['ul', 'ol']):
            for li in ul.find_all('li'):
                t = text_of(li)
                if not t:
                    continue
                t = re.sub(r'\*+$', '', t).strip()
                k = norm(t)
                if k and k not in local_seen:
                    local_seen.add(k)
                    all_items.append(f"- {t}")
        if all_items:
            content_parts.append(f"**{default_title}**")
            content_parts.append('\n'.join(all_items))
            return '\n'.join([p for p in content_parts if p]).strip()

    # ---------- 3c) Paragraph-only fallback (no lists/tables; just prose) ----------
    if not sections and intro_paras:
        return "\n\n".join(intro_paras)

    # ---------- 4+5) Repair + Render ----------
    if sections:
        # Keep current heuristics (e.g., promote Industrial Training → Specialisation if needed)
        sections = _repair_sections_towards_expected(sections)

        # Merge duplicate titles and de-dup items per title
        by_title = {}
        order_seen = []
        for _, title, items in sections:
            tnorm = normalize_heading(title)
            if not tnorm:
                continue
            if tnorm not in by_title:
                by_title[tnorm] = []
                order_seen.append(tnorm)
            seen_items = {re.sub(r'\s+', ' ', li).strip().lower() for li in by_title[tnorm]}
            for li in items:
                k = re.sub(r'\s+', ' ', li).strip().lower()
                if k and k not in seen_items:
                    by_title[tnorm].append(li)
                    seen_items.add(k)

        # If these markers slipped in as titles from the generic pass, drop them
        for bogus in ('Industrial Training', 'Capstone Project'):
            b = normalize_heading(bogus)
            if b in by_title and any(t.lower() == 'specialisation' for t in by_title.keys()):
                by_title.pop(b, None)
                if b in order_seen:
                    order_seen.remove(b)

        # Hard order for the main four blocks; others keep original appearance
        priority = {'core': 1, 'discipline core': 2, 'specialisation': 3, 'compulsory subjects': 4}
        ordered_titles = sorted(order_seen, key=lambda tt: (priority.get(tt.lower(), 999), order_seen.index(tt)))

        # Append sections in the enforced order (preserves the earlier intro line if any)
        for t in ordered_titles:
            items = [li for li in by_title[t]
                     if not re.search(r'uses\s+sas\s+content\s+and\s+software', li, re.I)]
            if items:
                content_parts.append(f"**{t}**")
                content_parts.append('\n'.join(items))

    return '\n\n'.join([p for p in content_parts if p]).strip()


def extract_content_from_div(div, context='general'):
    """Extract and format content from a div, with de-duplication for bullets.
    For Programme Structure, skip tables to avoid duplicate lists rendered in responsive layouts.
    """
    content_parts = []
    bullet_lines = []
    seen_bullets = set()

    def norm_bullet(s: str) -> str:
        # normalize for de-duplication
        s = re.sub(r'\s+', ' ', s).strip().lower()
        s = s.lstrip('-•* ').strip()
        return s

    # 1) Paragraphs (keep descriptive notes like 'Students are required...' and 'NOTE:')
    for p in div.find_all('p'):
        text = clean_text(p.get_text())
        if text:
            content_parts.append(text)

    # 2) Lists -> bullets, with de-dup
    for ul in div.find_all(['ul', 'ol']):
        # recursive=True to catch nested LIs cleanly
        for li in ul.find_all('li'):
            text = clean_text(li.get_text())
            if not text:
                continue
            k = norm_bullet(text)
            if k and k not in seen_bullets:
                seen_bullets.add(k)
                bullet_lines.append(f"- {text}")

    # 3) Tables
    if context != 'programme':
        for table in div.find_all('table'):
            table_content = extract_table_content(table)
            if not table_content:
                continue
            # split and dedupe bullet-like lines inside tables as well
            deduped = []
            for line in table_content.splitlines():
                if re.match(r'^\s*[-•*]\s+', line):
                    k = norm_bullet(line)
                    if k in seen_bullets:
                        continue
                    seen_bullets.add(k)
                deduped.append(line)
            if deduped:
                content_parts.append('\n'.join(deduped))

    # Append bullets after paragraphs
    if bullet_lines:
        content_parts.append('\n'.join(bullet_lines))

    return '\n\n'.join([part for part in content_parts if part]).strip()


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
    """Extract intake information from badges and text (excluding duration badges)."""
    intake_info = []

    # Find badge elements
    badges = soup.find_all('span', class_='badge')
    for badge in badges:
        text = clean_text(badge.get_text())
        if not text:
            continue

        lower = text.lower()

        # Skip mode badges
        if lower in ['full time', 'part time']:
            continue

        # NEW: skip badges that look like duration ("12-18 months", "2 years", etc.)
        if re.search(r'\b(month|months|year|years)\b', lower):
            continue

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

def extract_duration_info(soup):
    """Extract duration information separately (e.g., '12-18 months',
    '10 Teaching Weeks Programme', 'FOUR (4) Hours', 'Eight (8) Hours')."""

    # 1) Look for badges that clearly look like programme length (months/years)
    badges = soup.find_all('span', class_='badge')
    for badge in badges:
        text = clean_text(badge.get_text())
        if not text:
            continue

        lower = text.lower()

        # e.g. "12-18 months", "2 years"
        if re.search(r'\b(month|months|year|years)\b', lower):
            return text

    # 2) Look for "Duration: ..." in paragraph text
    for p in soup.find_all('p'):
        text = clean_text(p.get_text())
        if not text:
            continue
        m = re.search(r'Duration\s*[:\-]\s*(.+)', text, re.I)
        if m:
            return m.group(1).strip()

    # 3) Fallback: short lines mentioning weeks/hours, e.g.
    #    "10 Teaching Weeks Programme"
    #    "FOUR (4) Hours"
    #    "Eight (8) Hours"
    candidates = []
    for node in soup.find_all(['p', 'li', 'span']):
        text = clean_text(node.get_text())
        if not text:
            continue

        # We only want short, label-like lines, not long paragraphs
        if len(text) > 80:
            continue

        # Must contain week(s) or hour(s)
        if not re.search(r'\b(week|weeks|hour|hours)\b', text, re.I):
            continue

        # Must look like "number + unit" (number or word-number)
        if re.search(
            r'\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b'
            r'.{0,40}\b(week|weeks|hour|hours)\b',
            text,
            re.I
        ):
            candidates.append(text)

    if candidates:
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for t in candidates:
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(t)
        return '\n'.join(unique)

    return ""


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

        # Extract Programme Structure
        prog_structure = extract_tab_content(
            soup,
            'Programme Structure|Subjects Offered|Overall Course Structure|Skill You Will Learn|Subjects Offering|Course Outline'
        )
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

        # Extract Duration
        duration = extract_duration_info(soup)
        if duration:
            course_data['sections']['Duration'] = duration
            print("  ✓ Duration")
        else:
            print("  ✗ Duration - not found")

        # Extract Intake (badges & 'Next Intake', excluding duration badges)
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

            markdown_lines.append("### URL")
            markdown_lines.append("")
            markdown_lines.append(course['url'])
            markdown_lines.append("")

            section_order = [
                'Programme Structure',
                'Entry Requirements',
                'Fees',
                'Duration',
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
                    'url': course_data['url'],
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