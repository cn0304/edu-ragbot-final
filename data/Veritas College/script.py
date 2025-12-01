#!/usr/bin/env python3
"""
Usage:
    python3 "data/Veritas College/script.py" \
      "data/Veritas College/input.txt" \
      --output "data/Veritas College/Courses.md"
"""

import argparse
import re
import time
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup, Tag
from collections import defaultdict

# ---------------------------------------------------------------------------
# Some Veritas pages render Duration / Pathway / Study Mode / Intakes in
# JS widgets. We patch those slugs here.
# ---------------------------------------------------------------------------
MANUAL_META = {
    "diploma-in-early-childhood-education-conventional": {
        "Duration": "Full Time (2 years) & Part Time (4 years)",
        "Pathway": "Diploma",
        "Study Mode": "Conventional",
        "Intakes": "January, May & September",
    },
    "ba-hons-accounting-finance-conventional": {
        "Duration": "3 years",
        "Pathway": "3+0 Degree Programme",
        "Study Mode": "Blended Learning / Full Time",
        "Intakes": "January, May & September",
    },
    "ba-hons-accounting-finance-odl": {
        "Duration": "3 years",
        "Pathway": "3+0 Degree Programme",
        "Study Mode": "Online",
        "Intakes": "January, May & September",
    },
    "ba-hons-business-administration-odl": {
        "Duration": "3 years",
        "Pathway": "3+0 Degree Programme",
        "Study Mode": "Online",
        "Intakes": "January, May & September",
    },
    "bachelor-of-communication-hons-branding-digital-marketing-conventional": {
        "Duration": "3 years",
        "Pathway": "3+0 Degree Programme",
        "Study Mode": "Blended learning",
        "Intakes": "January, June & August",
    },
    "bachelor-of-communication-hons-branding-digital-marketing-odl": {
        "Duration": "3 years",
        "Pathway": "3+0 Degree Programme",
        "Study Mode": "Online",
        "Intakes": "January, June & August",
    },
    "bachelor-of-education-hons-early-childhood-education-conventional": {
        "Duration": "3 years",
        "Pathway": "3+0 Degree Programme",
        "Study Mode": "Blended learning/ Full Time",
        "Intakes": "January, May & September",
    },
    "bsc-hons-psychology-odl": {
        "Duration": "3 years",
        "Pathway": "3+0 Degree Programme",
        "Study Mode": "Blended learning",
        "Intakes": "January, May & September",
    },
    "master-of-corporate-law-governance-odl": {
        "Duration": "1.5 years",
        "Pathway": "Postgraduate",
        "Study Mode": "Online",
        "Intakes": "January, May & September",
    },
    "master-of-education-odl": {
        "Duration": "1.5 years",
        "Pathway": "Postgraduate",
        "Study Mode": "Blended learning",
        "Intakes": "January, May & September",
    },
    "mba-conventional": {
        "Duration": "16 months",
        "Pathway": "Postgraduate",
        "Study Mode": "Blended learning",
        "Intakes": "January, May & September",
    },
    "mba-in-corporate-law-odl": {
        "Duration": "18 months",
        "Pathway": "Postgraduate",
        "Study Mode": "Online",
        "Intakes": "January, May & September",
    },
    "mba-in-digital-marketing-odl": {
        "Duration": "18 months",
        "Pathway": "Postgraduate",
        "Study Mode": "Blended learning",
        "Intakes": "January, May & September",
    },
    "mba-in-digital-transformation-odl": {
        "Duration": "18 months",
        "Pathway": "Postgraduate",
        "Study Mode": "Blended learning",
        "Intakes": "January, May & September",
    },
    "doctor-of-business-administration-odl": {
        "Duration": "3 years",
        "Pathway": "Postgraduate",
        "Study Mode": "Blended learning",
        "Intakes": "January, May & September",
    },
    "phd-in-education-conventional": {
        "Duration": "Full-time (3-5 years)/ Part-time (4-7 years)",
        "Pathway": "Postgraduate",
        "Intakes": "January, May & September",
    },
    "phd-in-education-odl": {
        "Duration": "3 years (Full time)/ 4 years (Part time)",
        "Pathway": "Postgraduate",
        "Study Mode": "Blended learning",
        "Intakes": "January, May & September",
    },
}


NON_SUBJECT_TITLES = {
    "master of education",
    "phd in education",
    "mba (odl)",
    "mba in digital marketing (odl)",
    "mba in digital transformation (odl)",
    "postgraduate Diploma in Teaching & Learning",
    "MBA ( ODL)",
    "Doctor of Business Administration (ODL)",
    "Master of Education (ODL)",
    "Postgraduate Diploma in Teaching & Learning",
}

UNWANTED_FOOTER_ITEMS = {
    "Law",
    "Business",
    "Digital Marketing",
    "Digital Technology",
    "Early Childhood & Education",
    "Psychology",
    "Pre-University",
    "Diploma",
    "Degree",
    "Postgraduate",
    "Partners & Affiliates",
    "Learning Center",
    "ICYMI",
    "International Students",
    "Facilities",
    "Accommodation",
    "Terms",
    "Privacy Policy",
    "Refund Policy",
    "Health & Safety",
}

MBA_SLUG_OVERRIDES = {
    "mba": "master-of-business-administration-MBA",
    "mba-conventional": "master-of-business-administration-Conventional",
}

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()

FEE_HEADING_LINES = {
    "MALAYSIAN STUDENTS",
    "INTERNATIONAL STUDENTS",
    "STANDARD MALAYSIAN STUDENTS FEE",
    "STANDARD INTERNATIONAL STUDENTS FEE",
}

def _normalize_fee_headings(fee_text: str) -> str:
    """Convert Malaysian / International fee labels into smaller markdown headings."""
    if not fee_text:
        return fee_text

    lines = fee_text.splitlines()
    fixed = []

    for line in lines:
        stripped = line.strip()

        # If the line is exactly one of our known headings (case-insensitive),
        # turn it into an ATX heading, e.g. "#### STANDARD MALAYSIAN STUDENTS FEE"
        if stripped.upper() in FEE_HEADING_LINES:
            fixed.append(f"#### {stripped}")
            continue

        # If there are leftover '---' lines from older runs immediately
        # after one of our headings, skip them so we don't show extra rules.
        if stripped == "---" and fixed and fixed[-1].startswith("#### "):
            continue

        fixed.append(line)

    return "\n".join(fixed)

def _split_fee_paragraph(txt: str):
    """
    Split a fee paragraph like:
      'RM16,900 Resource Fee: RM1,000 EMGS Fee: RM450'
    into separate logical lines:
      ['RM16,900', 'Resource Fee: RM1,000', 'EMGS Fee: RM450']
    """
    markers = ["Resource Fee:", "EMGS Fee:"]
    parts = []
    rest = txt

    while True:
        idx = -1
        marker = None

        # find earliest marker in the remaining text
        for m in markers:
            j = rest.find(m)
            if j != -1 and (idx == -1 or j < idx):
                idx = j
                marker = m

        # no more markers – keep the remainder and stop
        if idx == -1:
            if rest.strip():
                parts.append(rest.strip())
            break

        # text before the marker (e.g. 'RM29,000')
        prefix = rest[:idx].strip()
        if prefix:
            parts.append(prefix)

        # move past the marker
        rest = rest[idx + len(marker):]

        # find where this marker's value ends (before next marker, if any)
        next_idx = -1
        for m in markers:
            j = rest.find(m)
            if j != -1 and (next_idx == -1 or j < next_idx):
                next_idx = j

        if next_idx == -1:
            value = rest.strip()
            rest = ""
        else:
            value = rest[:next_idx].strip()
            rest = rest[next_idx:]

        parts.append(f"{marker} {value}".strip())

    return [p for p in parts if p]

def _expand_fee_inline_markers(fee_text: str) -> str:
    if not fee_text:
        return fee_text

    new_lines = []
    for line in fee_text.splitlines():
        stripped = line.strip()
        if not stripped:
            # keep blank lines as-is
            new_lines.append(line)
            continue

        parts = _split_fee_paragraph(stripped)
        if len(parts) == 1:
            # nothing to split on this line
            new_lines.append(line)
        else:
            # each logical part becomes its own line
            for p in parts:
                new_lines.append(p)

    return "\n".join(new_lines)

def _extract_fee_from_headings(soup) -> str:
    """
    Fallback when there is no fee table/image but the page shows
    MALAYSIAN / INTERNATIONAL STUDENTS fee cards as headings.
    """
    heading_tags = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        label = clean_text(tag.get_text(" ", strip=True))
        if label.upper() in FEE_HEADING_LINES:
            heading_tags.append(tag)

    if not heading_tags:
        return ""

    lines = []

    for heading in heading_tags:
        label = clean_text(heading.get_text(" ", strip=True))
        # Keep plain text; _normalize_fee_headings will turn it into #### later.
        lines.append(label)
        lines.append("")

        # collect <p> until the next heading
        stop = None
        for h in heading.find_all_next(["h1", "h2", "h3", "h4", "h5", "h6"]):
            if h is heading:
                continue
            stop = h
            break

        current = heading
        while True:
            current = current.next_element
            if current is None or current is stop:
                break

            if isinstance(current, Tag) and current.name == "p":
                txt = clean_text(current.get_text(" ", strip=True))
                if txt:
                    parts = _split_fee_paragraph(txt)
                    if parts:
                        lines.extend(parts)

        lines.append("")

    return "\n".join(lines).strip()

def _extract_fee_from_fee_tables(soup) -> str:
    tables = soup.find_all("table", class_=re.compile("Theme-Layer-BodyText-Table"))
    if not tables:
        return ""

    fee_tables = []
    for table in tables:
        th = table.find("th")
        if not th:
            continue
        label = clean_text(th.get_text(" ", strip=True))
        if label.upper() in FEE_HEADING_LINES:
            fee_tables.append((table, label))

    if not fee_tables:
        return ""

    blocks = []

    for idx, (table, label) in enumerate(fee_tables):
        block_lines = []
        # keep plain label; _normalize_fee_headings will make it #### later
        block_lines.append(label)
        block_lines.append("")

        for p in table.find_all("p"):
            parts = list(p.stripped_strings)
            if not parts:
                continue

            # If this <p> is just the SAME heading as the table label,
            # skip it (we already added the label at the top of block_lines).
            if len(parts) == 1:
                only = parts[0].strip()
                if only.upper() == label.upper():
                    continue

            lower_join = " ".join(parts).lower()

            # e.g. combined text like
            #   'RM29,000 Resource Fee: RM1,000 per year'
            if "resource fee" in lower_join:
                amount = parts[0].strip()
                if amount:
                    block_lines.append(amount)
                extra = " ".join(parts[1:]).strip()
                if extra:
                    block_lines.append(extra)
            else:
                block_lines.append(" ".join(parts).strip())

        # Attach extra note paragraphs between this table and the next fee table
        # (e.g. EPF Withdrawals, instalment schemes).
        next_table = fee_tables[idx + 1][0] if idx + 1 < len(fee_tables) else None
        node = table.next_sibling
        while node is not None and node is not next_table:
            if isinstance(node, Tag) and node.name == "p":
                extra_txt = clean_text(node.get_text(" ", strip=True))
                if extra_txt:
                    block_lines.append(extra_txt)
            node = node.next_sibling

        blocks.append("\n".join(block_lines).rstrip())

    return "\n\n".join(blocks).strip()

# -------------------------- generic text parsers -------------------------- #

def _parse_semester_elective_block(text):
    if not text:
        return ""

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = []
    current_section = None
    i = 0

    while i < len(lines):
        line = lines[i]
        lower = line.lower()

        # Semester headings
        if re.match(r"semester\s+\d+", lower):
            if out:
                out.append("")
            out.append(f"#### {line}")
            current_section = "semester"
            i += 1
            continue

        # Elective heading (e.g. "Elective Modules")
        if "elective" in lower:
            if out:
                out.append("")
            out.append("##### Electives")
            current_section = "electives"
            i += 1
            continue

        if not current_section:
            i += 1
            continue

        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        if (
            line
            and len(line) <= 80
            and next_line
            and len(next_line) >= len(line) + 15
        ):
            out.append(f"- {line}")
            i += 2
            continue

        i += 1

    return "\n".join(out).strip()


def _parse_year_elective_mpu_block(text):
    if not text:
        return ""

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = []
    current_section = None

    for line in lines:
        lower = line.lower()

        if "here's the big picture" in lower or "heres the big picture" in lower:
            continue
        if "entry requirements" in lower:
            continue

        if re.match(r"year\s+\d+", lower):
            if out:
                out.append("")
            out.append(f"#### {line.strip()}")
            current_section = "year"
            continue

        if "elective" in lower:
            if out:
                out.append("")
            out.append("##### Electives")
            current_section = "elective"
            continue

        if "mpu" in lower:
            if out:
                out.append("")
            out.append("##### MPU Modules")
            current_section = "mpu"
            continue

        if current_section:
            out.append(f"- {line}")

    return "\n".join(out).strip()


def _extract_course_modules_from_h2(soup):
    heading = None
    for tag in soup.find_all(["h2", "h3"]):
        txt = tag.get_text(" ", strip=True)
        if re.search(r"\bcourse\s*modules\b", txt, re.I):
            heading = tag
            break

    if not heading:
        return ""

    chunks = []
    for node in heading.next_siblings:
        if node is None:
            continue
        name_attr = getattr(node, "name", None)
        name = name_attr.lower() if isinstance(name_attr, str) else ""
        if name in ("h1", "h2"):
            break

        if hasattr(node, "get_text"):
            txt = node.get_text("\n", strip=True)
        else:
            txt = str(node).strip()
        if txt:
            chunks.append(txt)

    raw = "\n".join(chunks)
    if not raw.strip():
        return ""

    lower_raw = raw.lower()
    if "year 1" in lower_raw or "year 2" in lower_raw:
        return _parse_year_elective_mpu_block(raw)
    return _parse_semester_elective_block(raw)


# ------------------------ Veritas “Year / MPU” parser ------------------------ #

def _extract_veritas_year_mpu_modules(soup):
    """
    Parse layouts with:
      - Year 1, Year 2, Year 3
      - MPU Modules
      - CORE SUBJECTS / ELECTIVE SUBJECTS blocks
      - COURSE MODULES
      - Areas of Concentration (Choose 1)
      - CORE MODULES (e.g. MBA)
      - SPECIALIST ELECTIVE PATHWAYS (e.g. MBA specialist tracks)
      - UPCOMING MBA SPECIALIZATIONS
    """
    headings = []
    for tag in soup.find_all(["h2", "h3"]):
        text = clean_text(tag.get_text(" ", strip=True))
        lower = text.lower()

        # Year 1 / Year 2 / Year 3
        if re.match(r"^year\s+\d+\b", lower):
            # Skip fee headers like "Year 1 RM21,000"
            if "rm" in lower:
                continue

            prev_heading = tag.find_previous(["h2", "h3"])
            is_elective_year = False
            elective_title = None
            if prev_heading:
                prev_text = clean_text(prev_heading.get_text(" ", strip=True))
                if re.search(r"\belective modules\b", prev_text, re.I):
                    is_elective_year = True
                    elective_title = prev_text

            headings.append(
                {
                    "tag": tag,
                    "is_elective_year": is_elective_year,
                    "elective_title": elective_title,
                }
            )

        # MPU section
        elif "mpu modules" in lower:
            headings.append(
                {"tag": tag, "is_elective_year": False, "elective_title": None}
            )

        # CORE SUBJECTS / ELECTIVE SUBJECTS
        elif "core subjects" in lower or "elective subjects" in lower:
            headings.append(
                {"tag": tag, "is_elective_year": False, "elective_title": None}
            )

        # Newer layouts:
        # - COURSE MODULES / Areas of Concentration
        # - CORE MODULES (MBA)
        # - SPECIALIST ELECTIVE PATHWAYS (MBA)
        # - UPCOMING MBA SPECIALIZATIONS
        elif (
            "course modules" in lower
            or "areas of concentration" in lower
            or "core modules" in lower
            or "specialist elective pathways" in lower
            or "upcoming mba" in lower
        ):
            headings.append(
                {"tag": tag, "is_elective_year": False, "elective_title": None}
            )

    if not headings:
        return ""

    lines = []

    for idx, info in enumerate(headings):
        tag = info["tag"]
        is_elective_year = info["is_elective_year"]
        elective_title = info["elective_title"]

        text = clean_text(tag.get_text(" ", strip=True))
        lower = text.lower()

        # Decide heading title + level
        if "mpu modules" in lower:
            heading_title = "MPU Modules"
            heading_level = 4
        else:
            if is_elective_year:
                heading_title = elective_title or "Elective Modules"
                heading_level = 5      # ##### Elective Modules*
            else:
                heading_title = text   # Year 1 / COURSE MODULES / CORE SUBJECTS / ...
                heading_level = 4

        lines.append("#" * heading_level + f" {heading_title}")

        # Next section boundary
        next_heading = headings[idx + 1]["tag"] if idx + 1 < len(headings) else None

        module_found = False

        # --- Normal case: collect modules from <h4> cards (Year / Core / Pathways) ---
        for node in tag.next_elements:
            if node is next_heading:
                break

            if getattr(node, "name", None) == "h4":
                module_title = clean_text(node.get_text(" ", strip=True))
                if not module_title:
                    continue

                # Skip progression degrees that are not actual subjects
                if module_title.lower() in NON_SUBJECT_TITLES:
                    continue

                # Collect ALL <p> siblings until the next heading (h4/h3/h2)
                desc_parts = []
                sib = node
                while True:
                    sib = sib.next_sibling
                    if sib is None:
                        break
                    name = getattr(sib, "name", None)
                    if name is None:
                        continue
                    name = name.lower()
                    if name in ("h4", "h3", "h2"):
                        break
                    if name == "p":
                        text_p = clean_text(sib.get_text(" ", strip=True))
                        if text_p:
                            desc_parts.append(text_p)

                desc = " ".join(desc_parts)

                if desc:
                    lines.append(f"- **{module_title}** — {desc}")
                else:
                    lines.append(f"- {module_title}")

                module_found = True

        # --- NEW: fallback for pages like mba-conventional ---
        # If this heading is "COURSE MODULES" and we did not see any <h4>,
        # treat all <li> items under this heading as module lines.
        if (not module_found) and "course modules" in heading_title.lower():
            for node in tag.next_elements:
                if node is next_heading:
                    break
                if getattr(node, "name", None) == "li":
                    txt = clean_text(node.get_text(" ", strip=True))
                    if txt:
                        lines.append(f"- {txt}")
                        module_found = True

        lines.append("")

    return "\n".join(lines).strip()


def _extract_veritas_elective_modules(soup):
    """
    Only handle simple 'Elective Modules' + <ul><li>...</li></ul> blocks
    inside a Theme-Layer-BodyText container (e.g. BA Business Admin ODL).
    """
    heading = None
    for tag in soup.find_all(["h2", "h3", "h4"]):
        txt = clean_text(tag.get_text(" ", strip=True))
        if re.search(r"\belective modules\b", txt, re.I):
            heading = tag
            break

    if not heading:
        return ""

    container = heading.find_parent("div", class_=re.compile(r"Theme-Layer-BodyText"))
    if not container:
        container = heading.parent

    ul = container.find("ul")
    if not ul:
        return ""

    lines = ["##### Elective Modules"]
    for li in ul.find_all("li"):
        item_text = clean_text(li.get_text(" ", strip=True))
        if item_text:
            lines.append(f"- {item_text}")

    return "\n".join(lines).strip()

def _format_course_offer_card(course):
    """
    Helper for Veritas MBA cards.

    Returns:
      - title: card title (h4 / h3 / h2)
      - desc: main description (without elective lines)
      - electives: list of lines that talk about Elective 1/2 or 'Electives: ...'
      - full_text_lower: all non-empty <p> text in lower-case (for filtering).
    """
    title_tag = course.find(["h4", "h3", "h2"])
    title = clean_text(title_tag.get_text()) if title_tag else ""

    desc = ""
    electives = []
    full_parts = []

    for p in course.find_all("p"):
        t = clean_text(p.get_text(" ", strip=True))
        if not t:
            continue

        # many cards repeat the title again as a <p> – skip exact duplicates
        if title and t.lower() == title.lower():
            continue

        full_parts.append(t)
        low = t.lower()

        # separate out elective lines
        if low.startswith("elective"):
            electives.append(t)
        elif low.startswith("electives"):
            electives.append(t)
        else:
            # normal description text
            if desc:
                desc += " " + t
            else:
                desc = t

    full_text_lower = " ".join(full_parts).lower()
    return title, desc, electives, full_text_lower


def _extract_veritas_mba_modules(soup):

    # 1) Find the key section headings
    core_heading = soup.find(["h2", "h3"], string=re.compile(r"\bcore modules\b", re.I))
    spec_heading = soup.find(["h2", "h3"], string=re.compile(r"specialist elective pathways", re.I))

    if not core_heading or not spec_heading:
        return ""

    # ---------- CORE MODULES → Programme Modules ----------
    core_modules = []

    # We stop core parsing when we hit the specialist section
    stop_for_core = spec_heading

    for node in core_heading.next_elements:
        if node is stop_for_core:
            break

        if getattr(node, "name", None) == "p":
            txt = clean_text(node.get_text(" ", strip=True))
            if not txt:
                continue

            lower = txt.lower()

            # Skip obvious non-titles
            if txt.startswith("(") and txt.endswith(")"):
                # awarding university line: (MacQuarie University) etc.
                continue
            if "university" in lower:
                continue
            if "mba advantage" in lower or "specialist pathways" in lower:
                continue

            words = txt.split()
            # Heuristic: module titles are short, capitalized phrases without '.' or ':'
            if (
                1 <= len(words) <= 6
                and "." not in txt
                and ":" not in txt
            ):
                # Avoid duplicates
                line = f"- **{txt}**"
                if line not in core_modules:
                    core_modules.append(line)

    # ---------- SPECIALIST PATHWAYS ----------
    spec_lines = []

    # Stop specialist parsing when we reach APEL / entry requirements
    stop_for_spec = soup.find(
        ["h2", "h3"],
        string=re.compile(r"fast track your|how do i get in", re.I),
    )

    for node in spec_heading.next_elements:
        if node is stop_for_spec:
            break

        if getattr(node, "name", None) == "h4":
            title = clean_text(node.get_text(" ", strip=True))
            if not title:
                continue

            desc = ""
            elective_texts = []

            # Walk siblings until the next heading
            sib = node
            while True:
                sib = sib.next_sibling
                if sib is None:
                    break

                name = getattr(sib, "name", None)

                if name:
                    lname = name.lower()
                    if lname in ("h4", "h3", "h2"):
                        break
                    text = clean_text(sib.get_text(" ", strip=True))
                else:
                    text = clean_text(str(sib))

                if not text:
                    continue

                low = text.lower()
                if "elective" in low:
                    elective_texts.append(text)
                else:
                    if not desc:
                        desc = text
                    else:
                        desc += " " + text

            line = f"- **{title}**"
            if desc:
                line += f" — {desc}"
            if elective_texts:
                # Append Elective 1 / Elective 2 / "Electives: To be confirmed"
                line += " " + " ".join(elective_texts)

            spec_lines.append(line)

    # ---------- Combine ----------
    lines = []
    if core_modules:
        lines.append("#### Programme Modules")
        lines.extend(core_modules)
        lines.append("")
    if spec_lines:
        lines.append("#### Specialist Pathways")
        lines.extend(spec_lines)
        lines.append("")

    return "\n".join(lines).strip()


def _extract_pix_tabs_modules(soup):
    pix_tabs = soup.find("div", class_=re.compile(r"pix_tabs_container"))
    if not pix_tabs:
        return ""

    lines = []
    tab_buttons = pix_tabs.find_all("a", class_=re.compile("pix-tabs-btn"))
    for btn in tab_buttons:
        title = clean_text(btn.get_text())
        if not title:
            continue

        lines.append(f"#### {title}")

        tab_id = btn.get("href", "").lstrip("#")
        if not tab_id:
            lines.append("")
            continue

        tab_content = soup.find("div", id=tab_id)
        if not tab_content:
            lines.append("")
            continue

        cm_tab = tab_content.find("div", class_=re.compile(r"\bcm-tab\b")) or tab_content

        core_ul = cm_tab.find("ul")
        if core_ul:
            for li in core_ul.find_all("li"):
                txt = clean_text(li.get_text())
                if txt:
                    lines.append(f"- {txt}")

        elect_text = cm_tab.find(string=re.compile(r"Electives", re.I))
        if elect_text:
            lines.append("")
            lines.append("##### Electives")

            parent = elect_text.parent
            for _ in range(3):
                name = getattr(parent, "name", "").lower()
                if name in ("p", "div"):
                    break
                parent = getattr(parent, "parent", None)
                if parent is None:
                    break

            ul = parent.find_next("ul") if parent is not None else None
            if ul:
                for li in ul.find_all("li"):
                    txt = clean_text(li.get_text())
                    if txt:
                        lines.append(f"- {txt}")

        lines.append("")

    return "\n".join(lines).strip()


# ----------------------------- main scraper ----------------------------- #

def extract_urls_from_file(input_file):
    urls = []
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("http"):
                urls.append(line)
    return urls


def extract_course_info(url):
    print(f"Fetching: {url}")

    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=30)
        res.raise_for_status()
    except Exception as e:
        print(f"  ✗ Error fetching: {e}")
        return None

    soup = BeautifulSoup(res.text, "html.parser")
    course_data = {"url": url, "sections": {}}

    # --- Duration / Pathway / Study Mode / Intakes ---
    duration = pathway = study_mode = intakes = ""
    info_divs = soup.find_all("div", class_="awa wh")
    for div in info_divs:
        text = div.get_text(" ", strip=True).lower()
        strong = div.find("strong")
        if "duration" in text:
            duration = clean_text(strong.get_text()) if strong else ""
        elif "pathway" in text:
            pathway = clean_text(strong.get_text()) if strong else ""
        elif "study mode" in text:
            study_mode = clean_text(strong.get_text()) if strong else ""
        elif "intakes" in text:
            intakes = clean_text(strong.get_text()) if strong else ""

    course_data["sections"]["Duration"] = duration or "result not found in online"
    course_data["sections"]["Pathway"] = pathway or "result not found in online"
    course_data["sections"]["Study Mode"] = study_mode or "result not found in online"
    course_data["sections"]["Intakes"] = intakes or "result not found in online"

    if duration:
        print("  ✓ Duration")
    if pathway:
        print("  ✓ Pathway")
    if study_mode:
        print("  ✓ Study Mode")
    if intakes:
        print("  ✓ Intakes")

    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    manual = MANUAL_META.get(slug)
    if manual:
        for key, value in manual.items():
            course_data["sections"][key] = value

    # --- Programme Structure ---
    structure_lines = []
    use_generic = True

    # Special case: PHD in Education (Conventional) – single Research Methodology module
    if slug == "phd-in-education-conventional":
        cm_heading = None
        heading_text = ""

        # We cannot use `string=` because the h2 is split across <strong>/<span>
        for tag in soup.find_all(["h2", "h3"]):
            txt = clean_text(tag.get_text(" ", strip=True))
            if re.search(r"\bcourse\s*module\b", txt, re.I):
                cm_heading = tag
                heading_text = txt
                break

        if cm_heading:
            # The 'Research Methodology' line is the first <p> after the heading
            p = cm_heading.find_next("p")
            module_title = clean_text(p.get_text(" ", strip=True)) if p else ""
            if module_title:
                block_lines = []
                if heading_text:
                    block_lines.append(f"#### {heading_text.title()}")
                    block_lines.append("")
                block_lines.append(f"- {module_title}")
                course_data["sections"]["Programme Structure"] = "\n".join(block_lines)
                print("  ✓ Programme Structure (PHD single module override)")
                use_generic = False

    # MBA page: use dedicated parser based on CORE / SPECIALIST sections
    if slug == "mba":
        mba_ps = _extract_veritas_mba_modules(soup)
        if mba_ps:
            course_data["sections"]["Programme Structure"] = mba_ps
            print("  ✓ Programme Structure (MBA override)")
            use_generic = False  # <— do NOT run generic logic if this worked
        else:
            print("  ✗ MBA override failed – trying generic Veritas parser")

    if use_generic:
        if slug in (
                "ba-hons-accounting-finance-conventional",
                "ba-hons-accounting-finance-odl",
                "ba-hons-business-administration-odl",
                "bachelor-of-communication-hons-branding-digital-marketing-conventional",
                "bachelor-of-communication-hons-branding-digital-marketing-odl",
                "bachelor-of-education-hons-early-childhood-education-conventional",
                "bsc-hons-psychology-odl",
                "master-of-corporate-law-governance-odl",
                "master-of-education-odl",
                "mba-conventional",
                "mba-in-corporate-law-odl",
                "mba-in-digital-marketing-odl",
                "mba-in-digital-transformation-odl",
                "doctor-of-business-administration-odl",
                "phd-in-education-conventional",
                "phd-in-education-odl"
        ):
            special_ps = _extract_veritas_year_mpu_modules(soup)

            electives_block = ""
            # only BA Business Admin ODL has extra bullet electives list
            if slug == "ba-hons-business-administration-odl":
                electives_block = _extract_veritas_elective_modules(soup)

            if special_ps and electives_block:
                combined_ps = special_ps + "\n\n" + electives_block
            elif special_ps:
                combined_ps = special_ps
            else:
                combined_ps = electives_block

            if combined_ps:
                course_data["sections"]["Programme Structure"] = combined_ps
                print("  ✓ Programme Structure (Year/MPU/Electives override)")
            else:
                course_data["sections"]["Programme Structure"] = "result not found in online"
                print("  ✗ Programme Structure override failed")

        else:
            structure_section = soup.find("h2", string=re.compile(r"Course\s*Modules", re.I))
            if not structure_section:
                structure_section = soup.find(
                    "div",
                    id=re.compile(r"section-.*"),
                    class_=re.compile("Theme-Section.*GridSection"),
                )

            if structure_section:
                for block in structure_section.find_all(["h3", "h4"]):
                    title = clean_text(block.get_text())
                    if title:
                        structure_lines.append(f"#### {title}")
                        ul = block.find_next("ul")
                        if ul:
                            for li in ul.find_all("li"):
                                structure_lines.append(f"- {clean_text(li.get_text())}")
                        structure_lines.append("")

                if not structure_lines:
                    text_block = _extract_course_modules_from_h2(soup)
                    if text_block:
                        course_data["sections"]["Programme Structure"] = text_block
                        print("  ✓ Programme Structure")
                    else:
                        for li in structure_section.find_all("li"):
                            structure_lines.append(f"- {clean_text(li.get_text())}")
                        course_data["sections"]["Programme Structure"] = (
                                "\n".join(structure_lines) or "result not found in online"
                        )
                        print("  ✓ Programme Structure")
                else:
                    course_data["sections"]["Programme Structure"] = (
                            "\n".join(structure_lines) or "result not found in online"
                    )
                    print("  ✓ Programme Structure")
            else:
                structure_section = soup.find("h3", string=re.compile(r"Course Modules", re.I))
                if structure_section:
                    modules_section = structure_section.find_parent()
                    if modules_section:
                        for semester_div in modules_section.find_all("div", class_="tab-pane"):
                            sem_title = semester_div.get("id", "").replace("-", " ").title()
                            if sem_title:
                                structure_lines.append(f"#### {sem_title}")
                            for li in semester_div.find_all("li"):
                                structure_lines.append(f"- {clean_text(li.get_text())}")
                            structure_lines.append("")

                    if not structure_lines:
                        pix_text = _extract_pix_tabs_modules(soup)
                        if pix_text:
                            course_data["sections"]["Programme Structure"] = pix_text
                            print("  ✓ Programme Structure")
                        else:
                            course_data["sections"]["Programme Structure"] = "result not found in online"
                            print("  ✗ Programme Structure not found")
                    else:
                        course_data["sections"]["Programme Structure"] = (
                                "\n".join(structure_lines) or "result not found in online"
                        )
                        print("  ✓ Programme Structure")
                else:
                    if not structure_lines:
                        pix_tabs = soup.find("div", class_=re.compile(r"pix_tabs_container"))
                        if pix_tabs:
                            tab_buttons = pix_tabs.find_all("a", class_=re.compile("pix-tabs-btn"))
                            for btn in tab_buttons:
                                tab_title = clean_text(btn.get_text())
                                if tab_title:
                                    structure_lines.append(f"#### {tab_title}")
                                    tab_id = btn.get("href", "").replace("#", "")
                                    tab_content = soup.find("div", id=tab_id)
                                    if tab_content:
                                        for block in tab_content.find_all("div", style=re.compile("line-height")):
                                            title_tag = block.find("p")
                                            if title_tag and title_tag.find("strong"):
                                                title = clean_text(title_tag.get_text())
                                                desc_tag = title_tag.find_next_sibling("p")
                                                desc = clean_text(desc_tag.get_text()) if desc_tag else ""
                                                structure_lines.append(f"- **{title}** — {desc}")
                                        structure_lines.append("")

                        if not structure_lines:
                            theme_sections = soup.find_all("div", class_=re.compile(r"Theme-Section Theme-TextSection"))
                            for section in theme_sections:
                                if section.find("h2", string=re.compile("Course Modules", re.I)):
                                    for h3 in section.find_all("h3"):
                                        year = clean_text(h3.get_text())
                                        if year:
                                            structure_lines.append(f"#### {year}")
                                            for h4 in section.find_all("h4"):
                                                title = clean_text(h4.get_text())
                                                desc_tag = h4.find_next_sibling("p")
                                                desc = clean_text(desc_tag.get_text()) if desc_tag else ""
                                                structure_lines.append(f"- **{title}** — {desc}")
                                            structure_lines.append("")

                        if not structure_lines:
                            for section in soup.find_all("div", class_=re.compile(r"Theme-Section Theme-TextSection")):
                                for h3 in section.find_all("h3"):
                                    year = clean_text(h3.get_text())
                                    if year:
                                        structure_lines.append(f"#### {year}")
                                        for h4 in section.find_all("h4"):
                                            title = clean_text(h4.get_text())
                                            desc_tag = h4.find_next_sibling("p")
                                            desc = clean_text(desc_tag.get_text()) if desc_tag else ""
                                            structure_lines.append(f"- **{title}** — {desc}")
                                        structure_lines.append("")

                        if not structure_lines:
                            course_offers = soup.find_all("div", class_=re.compile(r"the-course-offer"))
                            if course_offers:
                                structure_lines.append("#### Programme Modules")
                                for course in course_offers:
                                    title_tag = course.find("h4")
                                    uni_tag = course.find("h5")
                                    desc_tag = course.find("p")

                                    title = clean_text(title_tag.get_text()) if title_tag else ""
                                    uni = clean_text(uni_tag.get_text()) if uni_tag else ""
                                    desc = clean_text(desc_tag.get_text()) if desc_tag else ""

                                    # Fix MBA-style cards where the <h4> is empty
                                    # and the module name lives in <p>.
                                    if not title and desc:
                                        title, desc = desc, ""

                                    line = f"- **{title}**"
                                    if uni:
                                        line += f" ({uni})"
                                    if desc:
                                        line += f" — {desc}"
                                    structure_lines.append(line)

                                structure_lines.append("")

                    if structure_lines:
                        course_data["sections"]["Programme Structure"] = "\n".join(structure_lines)
                        print("  ✓ Programme Structure")
                    else:
                        course_data["sections"]["Programme Structure"] = "result not found in online"
                        print("  ✗ Programme Structure not found")

        # Clean footer/menu bullets that leak into Programme Structure
        ps_raw = course_data["sections"].get("Programme Structure", "")
        if slug == "mba-conventional" and ps_raw:
            cleaned = []
            for line in ps_raw.splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    label = stripped[2:].strip()
                    if label in UNWANTED_FOOTER_ITEMS:
                        # skip footer/menu item
                        continue
                cleaned.append(line)
            course_data["sections"]["Programme Structure"] = "\n".join(cleaned).rstrip()

        # generic-only fallback refinements
        ps_value = course_data["sections"].get("Programme Structure", "").strip()
        if not ps_value or ps_value == "result not found in online":
            text_block = _extract_course_modules_from_h2(soup)
            if text_block:
                course_data["sections"]["Programme Structure"] = text_block
                print("  ✓ Programme Structure (text fallback)")
            else:
                pix_text = _extract_pix_tabs_modules(soup)
                if pix_text:
                    course_data["sections"]["Programme Structure"] = pix_text
                    print("  ✓ Programme Structure (pix-tabs fallback)")

    # --- Entry Requirements ---
    entry_lines = []
    entry_section = None

    # 1) Standard "HOW DO I GET IN?" heading (H2 or H3)
    for tag in soup.find_all(["h2", "h3"]):
        text = tag.get_text(strip=True).lower()
        if "how do i get in" in text:
            entry_section = tag.find_parent()
            break

    # 2) New master layout wrapper: <div class="admission-section"> ... </div>
    if not entry_section:
        admission_div = soup.find("div", class_=re.compile(r"admission-section"))
        if admission_div:
            txt = admission_div.get_text(" ", strip=True)
            if re.search(r"how do i get in|entry\s*requirements", txt, re.I):
                entry_section = admission_div

    # 3) Older Theme-Layer-BodyText layouts
    if not entry_section:
        for div in soup.find_all("div", class_=re.compile("Theme-Layer-BodyText")):
            text = div.get_text(strip=True)
            if re.search(r"how do i get in|entry", text, re.I):
                entry_section = div
                break

    # 4) Generic Unitar-style fallback
    if not entry_section:
        for div in soup.find_all("div", class_=re.compile("wpb_text_column|Theme-Layer-BodyText", re.I)):
            text = div.get_text(" ", strip=True)
            if re.search(r"how do i get in|entry\s*requirements", text, re.I):
                entry_section = div
                break

    if entry_section:
        for li in entry_section.find_all("li"):
            entry_lines.append(f"- {clean_text(li.get_text())}")
        if not entry_lines:
            for p in entry_section.find_all("p"):
                entry_lines.append(f"- {clean_text(p.get_text())}")
        course_data["sections"]["Entry Requirements"] = (
                "\n".join(entry_lines) or "result not found in online"
        )
        print("  ✓ Entry Requirements")
    else:
        course_data["sections"]["Entry Requirements"] = "result not found in online"
        print("  ✗ Entry Requirements not found")

    # --- Fee (table or image) ---
    fee_content = ""

    # 1) Special handling for MALAYSIAN / INTERNATIONAL fee tables
    fee_content = _extract_fee_from_fee_tables(soup)
    if fee_content:
        print("  ✓ Fee (fee tables)")
    else:
        # 2) Generic table → markdown (for other layouts)
        fee_table = soup.find("table", class_=re.compile("Theme-Layer-BodyText-Table"))
        if fee_table:
            rows = []
            for tr in fee_table.find_all("tr"):
                cols = [clean_text(td.get_text()) for td in tr.find_all(["th", "td"])]
                if cols:
                    rows.append(" | ".join(cols))
            if rows:
                header = rows[0]
                separator = " | ".join(["---"] * len(header.split("|")))
                fee_content = "\n".join([header, separator] + rows[1:])
                print("  ✓ Fee (table)")
        else:
            fee_img = None
            for img in soup.find_all("img"):
                alt = img.get("alt", "").lower()
                src = img.get("src", "")
                if "fee" in alt or "fee" in src.lower():
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = "https://www.veritas.edu.my" + src
                    fee_img = src
                    break

            if fee_img:
                fee_content = f"![Fee Structure]({fee_img})"
                print("  ✓ Fee (image)")
            else:
                print("  ✗ Fee not found")

    # If still nothing, try heading-based fallback (non-table layouts)
    if not fee_content:
        fee_content = _extract_fee_from_headings(soup)
        if fee_content:
            print("  ✓ Fee (heading-based)")

    # Make headings nice: '#### MALAYSIAN STUDENTS', etc.
    fee_content = _normalize_fee_headings(fee_content)
    # Split 'RM16,900 Resource Fee: RM1,000 EMGS Fee: RM450' into separate lines
    fee_content = _expand_fee_inline_markers(fee_content)

    course_data["sections"]["Fee"] = fee_content or "result not found in online"
    return course_data


def get_program_type_and_name(url):
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    lower = slug.lower()

    if "foundation" in lower:
        ptype = "Foundation"
    elif "diploma" in lower:
        ptype = "Diploma"
    elif "degree" in lower or "bachelor" in lower or "bsc" in lower or "ba-hons" in lower:
        ptype = "Degree"
    elif "master" in lower:
        ptype = "Master"
    elif "phd" in lower or "doctor" in lower:
        ptype = "PHD"
    else:
        ptype = "Master"

    # only change the displayed slug for the two MBA pages
    slug_for_output = MBA_SLUG_OVERRIDES.get(slug, slug)
    return ptype, slug_for_output


def format_markdown(courses_by_type):
    lines = []
    order = ["Foundation", "Diploma", "Degree", "Master", "PHD", "Other"]

    for ptype in order:
        if ptype not in courses_by_type:
            continue
        lines.append(f"# {ptype}\n")
        for course in sorted(courses_by_type[ptype], key=lambda x: x["slug"]):
            lines.append(f"## {course['slug']}\n")
            lines.append("")
            lines.append("### URL\n")
            lines.append(f"{course['url']}\n")
            lines.append("")
            for section in [
                "Duration",
                "Pathway",
                "Study Mode",
                "Intakes",
                "Programme Structure",
                "Entry Requirements",
                "Fee",
            ]:
                lines.append(f"### {section}\n")
                lines.append(course["sections"].get(section, "result not found in online"))
                lines.append("")
            lines.append("---\n")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Veritas course scraper")
    parser.add_argument("input_file", help="Text file containing course URLs")
    parser.add_argument("--output", default="VeritasCourses.md", help="Output markdown file")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests (seconds)")
    args = parser.parse_args()

    urls = extract_urls_from_file(args.input_file)
    print(f"Found {len(urls)} course URLs.\n")

    courses_by_type = defaultdict(list)
    success, fail = 0, 0

    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}]")
        info = extract_course_info(url)
        if info:
            ptype, slug = get_program_type_and_name(url)
            courses_by_type[ptype].append(
                {"slug": slug, "url": url, "sections": info["sections"]}
            )
            success += 1
        else:
            fail += 1
        if i < len(urls):
            time.sleep(args.delay)

    print(f"\nCompleted: {success} success, {fail} failed.\n")
    if success > 0:
        markdown = format_markdown(courses_by_type)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"✓ Output written to {args.output}")
    else:
        print("No successful extractions.")


if __name__ == "__main__":
    exit(main())
