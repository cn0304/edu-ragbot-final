#!/usr/bin/env python3
"""
PIDC Course Markdown Generator
Fetches fee tables (local & international) from PIDC website and
embeds them into hardcoded course markdown content.

Usage:
    python3 "data/Penang International Dental College/script.py" \
      "data/Penang International Dental College/input.txt" \
      --output "data/Penang International Dental College/Courses.md"

"""

import argparse
import requests
from bs4 import BeautifulSoup


def fetch_html(url):
    """Fetch page HTML with error handling."""
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[!] Error fetching {url}: {e}")
        return ""


def extract_fee_table(html):
    """Convert all HTML tables in the page into Markdown format."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return "result not found in online"

    markdown_output = ""
    for table in tables:
        rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True).replace("\n", " ") for td in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)

        if rows:
            markdown_output += "| " + " | ".join(rows[0]) + " |\n"
            markdown_output += "| " + " | ".join(["---"] * len(rows[0])) + " |\n"
            for row in rows[1:]:
                markdown_output += "| " + " | ".join(row) + " |\n"
            markdown_output += "\n"

    return markdown_output.strip() if markdown_output else "result not found in online"


def main():
    parser = argparse.ArgumentParser(description="Convert PIDC course info into Markdown format.")
    parser.add_argument("input", help="Text file containing URLs for local & international fees")
    parser.add_argument("--output", "-o", required=True, help="Output Markdown file")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip()]

    fee_local = "result not found in online"
    fee_international = "result not found in online"

    for url in urls:
        html = fetch_html(url)
        if "malaysian" in url:
            print("[+] Extracting Malaysian fee table...")
            fee_local = extract_fee_table(html)
        elif "international" in url:
            print("[+] Extracting International fee table...")
            fee_international = extract_fee_table(html)

    # --- Hardcoded content for Markdown ---
    md = f"""# Degree

## Doctor-of-dental-surgery

### URL
https://www.pidc.edu.my/dds/

### Programme Structure
**YEAR 1**
- Anatomy
- Physiology
- Biochemistry
- Oral Biology  

**YEAR 2**
- Microbiology
- Pathology
- Pharmacology
- Dental Materials & Equipment

**YEAR 3**
- General Surgery
- General Medicine
- Oral Pathology & Oral Medicine
- Dental Radiology  

**YEAR 4**
- Prosthetic Dentistry
- Peadiatric Dentistry
- Periodontology  

**YEAR 5**
- Orthodontics
- Oral Maxillofacial Surgery
- Conservative Dentistry
- Dental Public Health
- Family Dentistry  

### Entry Requirements
- Matriculation / Foundation in Science / Pre-Medical course:	CGPA 3.0 ( out of 4 ) in 3 subjects i.e. Biology and Chemistry and Physics or Mathematics.
- A-Levels / STPM:	Grades BBB, ABC or AAC in 3 subjects i.e.Biology, Chemistry, Physics or Mathematics and Advanced Mathematics.
- Unified Examination Certificate ( UEC ):	B4 each in 5 subjects i.e. Biology, Chemistry, Physics, Mathematics and Additional Mathematics,
- Monash University Foundation Pre-University / Program ( MUFY ) / University of New South Wales ( UNSW ) Foundation / Western Australia Curriculum Council / HSC Sydney Australia / Trinity College Foundation Studies / Australian Universities Foundation Programmes / South Australian Matriculation / Victorian Certificate of Education, Australia Year 12:	Aggregate or average of 80% in any 3 subjects i.e.Biology, Chemistry, Physics or Mathematics or 80% ATAR provided the subjects include Biology and Chemistry and Physics or Mathematics.
- National Certificate of Educational Achievement ( NCEA ) Level 3 or New Zealand Bursary:	Average of 80% in any 3 subjects i.e Biology, Chemistry, Physics or Mathematics.
- Canadian Pre-University ( CPU ) / Canadian International Matriculation Program ( CIMP ) / Canadian Grade 12 / 13:	Average of 80% in any 3 subject i.e. Biology, Chemistry, Physics or Mathematics.
- Indian Pre-University:	Average of 70% in any subjects (with an average of 70% in 3 subjects Biology, Chemistry, Physics or Mathematics ) International Baccalaureate (16)
- Diploma in Health Sciences:	( course >2 year or 5 semester in the same institutions ) CGPA 3.0 ( out of 4.0 ) and minimum of any 2Bs in Biology and/or Chemistry and/or Physics and Credit in English and Mathematics or Additional Mathematics and other subjects in SPM.
- Degree in Medical Sciences:	CGPA 3.0 ( out of 4 ) 5-year undergraduate medical programme
- Degree in the Arts or Humanities:	CGPA 4.0 (out of 4) 5-year undergraduate medical programme and 3 Cs in Biology, Chemistry, Physics, General Science, Mathematics or Additional Mathematics at SPM or its equivalent or an accredited bridging course
#### Those with matriculation, foundation or pre-medical programme, shall have passed and attend a minimum of the following grades at School Certificate level or its equivalent :
- Sijil Pelajaran Malaysia ( SPM ) / General Certificate of Education Ordinary ( “0” ) Levels:	5 Bs each in Biology and Chemistry and Physics and Mathematics (or Additional Mathematics) and another subject.
- Unified Examination Certificate (UEC):	B4 each in 3 subjects i.e. Biology, Chemistry, Physics or Mathematics or Additional Mathematics and another subject
- Other equivalent qualifications acceptable by MQA/MOHE or relevant professional bodies.  

### Fees

**LOCAL STUDENT**  
{fee_local}

**INTERNATIONAL STUDENT**  
{fee_international}

### Intake
September every year

### Duration
Five (5) years Dental Degree Programme

---

# Foundation

## foundation-in-science-programme

### URL
https://www.pidc.edu.my/fis/


### Programme Structure
PIDC's FOUNDATION IN SCIENCE PROGRAMME  
Duration: 1 year (3 semesters)  

**Semester 1**
- English I
- Thinking Skills
- Biology I
- Physics I
- Basic ICT  

**Semester 2**
- Chemistry I
- Mathematics I
- Biology II
- Introduction to Psychology
- Physic II
- Introduction to Programming

**Semester 3**
- Chemistry II
- Mathematics II
- English II
- Co-Curriculum
- Biochemistry
- Engineering Mathematics,  

Total Credit: 50  
Assessment: Continuous 50–60%, Final 40–50%  

### Entry Requirements
- SPM/O Level: Minimum 5 credits including Mathematics and 2 science subjects with passes in Bahasa Melayu and English; or  
- UEC: Minimum Grade B in Mathematics and 2 science subjects; or  
- Equivalent qualifications recognized by MQA/MOHE.  
- For Dentistry/Medicine/Pharmacy: Minimum 5Bs in Biology, Chemistry, Physics, Mathematics (or Add. Math), and one more subject.

### Fees
result not found in online

### Intake
May, June, August

---
## foundation-in-arts-programme

### URL
https://www.pidc.edu.my/fia/

### Entry Requirement
SPM / O-LEVEL: Pass with 5 Credits in any subjects.



"""

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(md.strip())

    print(f"[✓] Markdown file saved as {args.output}")


if __name__ == "__main__":
    main()