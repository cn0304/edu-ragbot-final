# backend/app/rag_engine.py
import os
from typing import List, Dict, Optional, AsyncGenerator, Tuple
import chromadb
from chromadb.config import Settings as ChromaSettings
import ollama
import re
import math, difflib
import logging, json, time, uuid
from pathlib import Path
from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
from llama_index.core.base.embeddings.base import BaseEmbedding

# Vector store adapter to re-use your existing Chroma collection
from llama_index.core import VectorStoreIndex, StorageContext, Settings as LISettings
from llama_index.vector_stores.chroma import ChromaVectorStore
# We'll use Ollama for embeddings so indexing/retrieval remains local
from llama_index.embeddings.ollama import OllamaEmbedding
# (We still use your own prompt + ollama.chat for generation, so no LI LLM needed.)
from llama_index.core.schema import NodeWithScore
from llama_index.core.vector_stores.types import MetadataFilters, ExactMatchFilter

# -----------------------------------------------------------------------------

OLLAMA_OPTIONS = {
    "temperature": 0,
    "top_p": 1,
    "seed": 123,
    "repeat_penalty": 1.0,
}

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


LLM_KEEP_ALIVE = os.getenv("LLM_KEEP_ALIVE", "15m")
LLM_NUM_CTX = _env_int("LLM_NUM_CTX", 2048)
LLM_NUM_PREDICT = _env_int("LLM_NUM_PREDICT", 384)
LLM_NUM_BATCH = _env_int("LLM_NUM_BATCH", 64)

# merge into the options sent to Ollama
OLLAMA_OPTIONS.update({
    "num_ctx": LLM_NUM_CTX,
    "num_predict": LLM_NUM_PREDICT,
    "num_batch": LLM_NUM_BATCH,
})

# ---- Fast, lossless LLM echo helpers (safe) ----
ECHO_SEGMENT_CHARS = _env_int("LLM_ECHO_MAX_CHARS", 1200)  # ~350 tokens/segment


def _approx_tokens(chars: int) -> int:
    # rough: 1 token ≈ 3.5 chars
    return max(64, int(chars / 3.5) + 32)


def _split_for_llm(text: str, max_chars: int = ECHO_SEGMENT_CHARS) -> list:
    """Split large markdown into <= max_chars chunks along paragraph boundaries."""
    parts, cur, size = [], [], 0
    for block in (text or "").split("\n\n"):
        b = (block or "").rstrip()
        blen = len(b)
        if size and (size + blen + 2 > max_chars):
            parts.append("\n\n".join(cur).rstrip())
            cur, size = [b], blen
        else:
            cur.append(b)
            size += (blen + 2)
    if cur:
        parts.append("\n\n".join(cur).rstrip())
    return [p for p in parts if p and p.strip()]


def _stable_tie_key(meta: Dict) -> tuple:
    meta = meta or {}
    return (
        meta.get('university_short') or '',
        meta.get('document_type') or '',
        meta.get('section') or '',
        meta.get('course_id') or '',
        meta.get('course_title') or ''
    )


try:
    from .reranker import create_reranker, BaseReranker

    RERANKER_AVAILABLE = True
except ImportError:
    RERANKER_AVAILABLE = False
    print("⚠ Reranker not available. Install: pip install sentence-transformers torch")

MD_LINK_RE = re.compile(r'\[[^\]]*\]\((?P<url>https?://[^)\s]+)\)')
BARE_URL_RE = re.compile(r'(?P<url>https?://[^\s)]+)')

MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\((?P<url>https?://[^)\s]+)\)')

def _extract_md_image_urls(text: str) -> list:
    """Return all image URLs from markdown image tags like ![Alt](https://...)."""
    urls = []
    for m in MD_IMAGE_RE.finditer(text or ""):
        u = (m.group("url") or "").strip()
        if u and u not in urls:
            urls.append(u)
    return urls

# Fix merged headings like "Year 1## Semester 1" or "Core## Semester 1"
_HEADING_RUN_RE = re.compile(
    r'(?m)([^\n#][^\n#]*?)\s*##\s*(\S[^\n]*)'
)
def _fix_heading_runs(text: str) -> str:
    if not text:
        return ""

    def repl(m: re.Match) -> str:
        before = (m.group(1) or "").rstrip()
        heading = (m.group(2) or "").lstrip()
        # We only want the second part as a plain line heading, NOT as "## Heading"
        return f"{before}\n\n{heading}"

    return _HEADING_RUN_RE.sub(repl, text)

def _postprocess_answer_text(text: str) -> str:
    """
    Fix small formatting glitches in LLM answers.

    Example:
    'Year 1## Semester 1'  →  'Year 1' + blank line + '## Semester 1'
    """
    if not text:
        return text

    # Fix ROYAL pattern: Year X## Semester Y → Year X\n\n## Semester Y
    text = re.sub(
        r"(Year\s+\d+)\s*##\s*(Semester\s+\d+)",
        r"\1\n\n## \2",
        text,
        flags=re.IGNORECASE,
    )

    return text

# ---- ONLY accept '### URL' blocks for simple docs (how_to_apply / scholarship / campus) ----
_HEADING_URL_RE = re.compile(r'(?im)^[ \t]*###\s*URL\s*(?:\n+|\s+)(https?://[^\s)\]]+)')

# Remove the '### URL' heading + its URL (same-line OR next-line)
_STRIP_HEADING_URL_BLOCK_RE = re.compile(
    r'(?im)(^[ \t]*###\s*URL\s+https?://[^\s)\]]+[^\n]*\n?'
    r'|^[ \t]*###\s*URL[ \t]*\n+[ \t]*https?://[^\s)\]]+[^\n]*\n?)'
)


# ---- URL extractors used across doc types ----
def _extract_any_urls(text: str) -> list:
    """Collect URLs from '### URL' blocks, markdown links, and bare URLs."""
    seen, out = set(), []
    for m in _HEADING_URL_RE.finditer(text or ""):
        u = (m.group(1) or "").strip()
        if u and u not in seen:
            seen.add(u);
            out.append(u)
    for m in MD_LINK_RE.finditer(text or ""):
        u = (m.group('url') or "").strip()
        if u and u not in seen:
            seen.add(u);
            out.append(u)
    for m in BARE_URL_RE.finditer(text or ""):
        u = (m.group('url') or "").strip()
        if u and u not in seen:
            seen.add(u);
            out.append(u)
    return out


# ---- URL scoring & collection (robust source attribution) ----
def _best_course_urls_from_chunks(chunks, query_info, limit=6):
    """
    Pick the best URLs from a set of chunks.
    """
    urls = []
    seen = set()

    # Target doc_type, course and uni (if known)
    doc_type = (query_info.get("doc_type") or "").lower()
    target_course = query_info.get("course_id_filter")
    target_uni = (query_info.get("university") or "") or None
    if target_uni:
        target_uni = target_uni.upper()

    # If no course_id_filter, try to infer dominant course_id
    if doc_type == "courses" and not target_course:
        counts = {}
        for ch in (chunks or [])[:5]:  # look at top few chunks
            md = ch.get("metadata") or {}
            cid = md.get("course_id")
            if not cid:
                continue
            counts[cid] = counts.get(cid, 0) + 1
        if counts:
            # Pick the course_id that appears most often
            target_course = max(counts.items(), key=lambda kv: kv[1])[0]

    def add_url(u: str, meta: dict):
        if not u or u in seen:
            return

        # For course queries, enforce university filter if we know it
        if target_uni:
            uni_short = (meta.get("university_short") or "").upper()
            if uni_short and uni_short != target_uni:
                return

        seen.add(u)
        urls.append({
            "university": meta.get("university_short"),
            "document": meta.get("document_type") or query_info.get("doc_type"),
            "course": meta.get("course_id"),
            "section": meta.get("section"),
            "url": u,
        })

    for ch in chunks or []:
        md = ch.get("metadata") or {}
        cid = md.get("course_id")

        # For course questions: if we know the target course, ignore other courses
        if doc_type == "courses" and target_course and cid and cid != target_course:
            continue

        # 1) Prefer URL-like fields in metadata
        for key in ("url", "source_url", "page_url", "pdf_url"):
            if md.get(key):
                add_url(md[key], md)
                break

        # 2) Also scan the text for URLs (e.g. ### URL blocks)
        txt = ch.get("content") or ch.get("text") or ch.get("document") or ""
        for u in _extract_any_urls(txt):
            add_url(u, md)

        if len(urls) >= limit:
            break

    return urls[:limit]


def _extract_heading_urls(text: str) -> list:
    seen, out = set(), []
    for m in _HEADING_URL_RE.finditer(text or ""):
        u = (m.group(1) or "").strip()
        if u and u not in seen:
            seen.add(u);
            out.append(u)
    return out


def _strip_heading_url_blocks(text: str) -> str:
    return _STRIP_HEADING_URL_BLOCK_RE.sub('', text or '')


def _slugify(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return s


def _url_seems_for_course(url: str | None, course_id: str | None, course_title: str | None) -> bool:
    if not url:
        return False
    path = (url or "").lower()

    cid = _slugify(course_id)
    ctitle = _slugify(course_title)

    # Strong positive signals
    if cid and cid in path:
        return True
    if ctitle and ctitle in path:
        return True

    # Token-based fallback (e.g., pick 'computer'/'science' in the URL)
    tokens = [t for t in re.split(r'[-/_]', ctitle) if len(t) >= 4]
    if tokens and any(t in path for t in tokens):
        return True

    # Otherwise, treat it as unrelated (prevents grabbing cross-links like IT when we asked for CS)
    return False


# ---- Generic normalizers for program matching (no hard-coded synonyms) ----
def _normalize_program_phrase(s: str) -> str:
    s = (s or "").lower()
    s = s.replace("e-", "e")  # e-business -> ebusiness
    s = re.sub(r'[^a-z0-9\s]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _rootish(w: str) -> str:
    # ultra-light stemming to group variants (studies/study, engineering/engineer, etc.)
    w = re.sub(r'ies$', 'y', w)
    w = re.sub(r'(ing|ers|er|ment|ments|tion|tions|ities|ity|al|als)$', '', w)
    w = re.sub(r's$', '', w)
    return w


def _token_roots_from_text(s: str) -> list:
    s = _normalize_program_phrase(s)
    toks = [t for t in re.findall(r'[a-z0-9]+', s) if len(t) > 2]
    drop = {
        'program', 'programme', 'programs', 'programmes', 'course', 'courses',
        'degree', 'diploma', 'certificate', 'foundation', 'pre', 'university',
        'bachelor', 'master', 'masters', 'doctor', 'phd',
        'in', 'of', 'for', 'with', 'and', 'about', 'the', 'a', 'an', 'studies'
    }
    out = []
    for t in toks:
        if t in drop:
            continue
        out.append(_rootish(t))
    return out


def _cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# --- University keyword aliases (extend as needed) ---
UNIVERSITY_ALIASES = {
    'uow': 'UOW', 'wollongong': 'UOW',
    'inti': 'INTI',
    'unitar': 'UNITAR',
    'msu': 'MSU', 'management and science': 'MSU',
    'mckl': 'MCKL', 'methodist college kuala lumpur': 'MCKL',
    'psdc': 'PSDC', 'penang skills development': 'PSDC',
    'pidc': 'PIDC',
    'sentral': 'SENTRAL', 'sentral college': 'SENTRAL',
    'toa': 'TOA', 'the one academy': 'TOA',

    # ATC – lock to "Advance Tertiary College"
    'atc': 'ATC',
    'advance tertiary': 'ATC',
    'advance tertiary college': 'ATC',
    'advance tertiary coll': 'ATC',

    # TAR UMT / TAR UC family normalized to TARU
    'taru': 'TARU',
    'tunku abdul rahman university': 'TARU',
    'taruc': 'TARU',
    'tarc': 'TARU',
    'tar umt': 'TARU',
    'tarumt': 'TARU',

    'veritas': 'VERITAS',
    'royal': 'ROYAL',
    'peninsula': 'PENINSULA',
    'peninsula college': 'PENINSULA',
}

# Canonical display names (used everywhere we show a uni name)
CANONICAL_UNI_NAMES = {
    'ATC': 'Advance Tertiary College',
    'UOW': 'University of Wollongong (Malaysia)',
    'INTI': 'INTI International University & Colleges',
    'UNITAR': 'UNITAR',
    'MSU': 'Management and Science University',
    'MCKL': 'Methodist College Kuala Lumpur',
    'PSDC': 'Penang Skills Development Centre',
    'PIDC': 'Penang International Dental College',
    'SENTRAL': 'SENTRAL College',
    'TOA': 'The One Academy',
    'TARU': 'Tunku Abdul Rahman University',
    'ROYAL': 'Royal College',
    'VERITAS': 'Veritas College',
    'PENINSULA': 'Peninsula College',
}


def _canonical_uni_display(short: Optional[str]) -> str:
    s = (short or '').strip().upper()
    name = CANONICAL_UNI_NAMES.get(s)
    return f"{s} ({name})" if name else (s or "?")


def _uni_name_policy_block() -> str:
    lines = [
        "University name policy:",
        "- Use ONLY the exact names below when referring to institutions.",
        "- Do NOT invent or guess expansions. If unsure, write only the short code (e.g., 'ATC').",
        "Allowed forms:",
    ]
    for code, name in sorted(CANONICAL_UNI_NAMES.items()):
        lines.append(f"- {code} — {name}")
    return "\n".join(lines)


def _normalize_uni_mentions(text: str) -> str:
    # Enforce canonical "(CODE (Name))" if LLM/deterministic text tries to expand differently
    import re as _re
    s = text or ""
    for code, name in CANONICAL_UNI_NAMES.items():
        # If we see "CODE (anything)", rewrite to "CODE (Canonical Name)"
        s = _re.sub(rf'\b{_re.escape(code)}\s*\((?:[^)]+)\)', f'{code} ({name})', s)
    return s


FEE_SECTION_SYNONYMS = [
    'Fee', 'Fees', 'Fee & Intakes', 'Fees & Intakes', 'Fee & Intake', 'Fees & Intake',
    'Tuition', 'Tuition Fee', 'Tuition Fees',
    'Estimated Fee', 'Estimated Fees',
    'Programme Fee', 'Programme Fees',
    'Program Fee', 'Program Fees',
    'Fee Structure', 'Tuition & Fees', 'Financial Info', 'Financial Information',
    'Fees & Scholarships', 'Scholarships & Fees', 'Programme Information & Fees',
    'Programme Info & Fees', 'Program Information & Fees', 'Program Info & Fees'
]

# Canonical course-type synonyms used for strict Chroma where filters.
COURSE_TYPE_SYNONYMS = {
    # Foundation & Pre-U
    "foundation": [
        "foundation", "Foundation",
        "Foundation & Pre-U", "Foundation & Pre-U",
        "Pre-University", "Pre University", "pre-university", "pre university",
        "A-Level", "A Levels", "A-Levels", "A Level", "CAL"
    ],

    # Certificate
    "certificate": [
        "certificate", "Certificate", "cert", "Cert"
    ],

    # Diploma
    "diploma": [
        "diploma", "Diploma"
    ],

    # Degree (Bachelor-level)
    "degree": [
        "degree", "Degree",
        # some datasets unfortunately store these in course_type
        "Bachelor", "Bachelors", "Bachelor's",
        "BSc", "B.Sc", "BA", "B.A", "BEng", "B.Eng",
        "Honours", "Honors", "Hons", "Honours (Hons)"
    ],

    # Master
    "master": [
        "master", "Master", "masters", "Masters", "Master's",
        "MSc", "M.Sc", "MBA", "MEng", "M.Eng"
    ],

    # PhD / Doctorate
    "phd": [
        "phd", "PhD", "Ph.D",
        "Doctor", "Doctoral", "Doctorate", "Doctor of Philosophy"
    ],

    # Pathway / ADTP (rare; keep for completeness)
    "pathway": [
        "pathway", "Pathway",
        "American Degree Transfer Program", "American University Program",
        "ADTP", "ADP"
    ],
}


def _detect_universities_in_text(text: str) -> list:
    t = (text or '').lower()
    found = []
    for key, short in UNIVERSITY_ALIASES.items():
        if key in t and short not in found:
            found.append(short)
    return found


# Accept commas, dots, and stray spaces inside the number
_money_re = re.compile(r'rm\s*([0-9][0-9,\.\s]*)', re.IGNORECASE)


def _parse_rm_amount(s: str) -> Optional[float]:
    m = _money_re.search(s or '')
    if not m:
        return None
    raw = m.group(1)
    # Normalize "19,1 00" -> "19100", keep decimals if present
    cleaned = re.sub(r'[\s,]', '', raw)
    try:
        return float(cleaned)
    except Exception:
        # Last-ditch: strip everything except digits and the first dot
        cleaned = re.sub(r'[^0-9\.]', '', raw)
        if cleaned.count('.') > 1:
            first = cleaned.find('.')
            cleaned = cleaned[:first + 1] + cleaned[first + 1:].replace('.', '')
        try:
            return float(cleaned)
        except Exception:
            return None


def _find_all_rm_amounts_with_pos(s: str) -> List[Tuple[int, float]]:
    out = []
    for m in _money_re.finditer(s or ''):
        raw = m.group(1)
        cleaned = re.sub(r'[\s,]', '', raw)
        try:
            val = float(cleaned)
            out.append((m.start(), val))
        except Exception:
            pass
    return out


# --- Special handling for "X Subjects - RM Y" fee tables (e.g., INTI CAL) ---
_INSERT_SPACE_BEFORE_SUBJECTS = re.compile(
    r'(\brm\s*[0-9][0-9,\.\s]*)(?=\d\s*subjects)',
    re.IGNORECASE
)

_SUBJECT_PLAN_RE = re.compile(
    r'(?i)\b([2-6])\s*subjects?\b[^r]*?rm\s*([0-9][0-9,\.\s]*)'
)


def _normalize_subject_plan_text(s: str) -> str:
    """Fix flattened tables like 'RM 21,0874 Subjects' by inserting a space before '4 Subjects'."""
    return _INSERT_SPACE_BEFORE_SUBJECTS.sub(r'\1 ', s or '')


def _extract_subject_plan_amounts(s: str) -> list[float]:
    """Return a list of RM amounts found in 'X Subjects - RM Y' patterns (values only)."""
    vals = []
    for m in _SUBJECT_PLAN_RE.finditer(s or ''):
        raw = m.group(2)
        try:
            vals.append(float(re.sub(r'[\s,]', '', raw)))
        except Exception:
            pass
    return vals


def _looks_like_structure_text(txt: str) -> bool:
    if not txt: return False
    low = txt.lower()
    if re.search(r'\b(semester|trimester|term|year)\s+[1-9]\b', low): return True
    lines = [l.strip() for l in low.splitlines() if l.strip()]
    shortish = [l for l in lines if 2 <= len(l) <= 64]
    bullets = [l for l in shortish if re.match(r'^(\-|\*|•|\d+[.)])\s*\w', l)]
    key = any(k in low for k in ["module", "subject", "curriculum", "core", "elective", "mpu"])
    return (len(bullets) >= 6 and key) or (len(shortish) >= 10 and "rm" not in low and "fee" not in low)



def _looks_like_fees_text(txt: str) -> bool:
    low = (txt or "").lower()
    return 'rm' in low and any(k in low for k in ['fee', 'fees', 'tuition', 'per semester', 'per year', 'per annum'])


def _looks_like_entry_text(txt: str) -> bool:
    low = (txt or "").lower()
    return any(k in low for k in ['entry requirement', 'entry requirements', 'stpm', 'a-level', 'spm', 'ielts', 'muet'])


def _looks_like_intakes_text(txt: str) -> bool:
    low = (txt or "").lower()

    # direct keyword hits (original behaviour)
    if any(k in low for k in ['intake', 'intakes', 'start date', 'start month']):
        return True

    # NEW: many sites put only months under an "Intake" heading
    months = [
        'january', 'february', 'march', 'april', 'may', 'june',
        'july', 'august', 'september', 'october', 'november', 'december'
    ]
    month_hits = sum(1 for m in months if m in low)

    # Treat blocks with at least 2 month names as an intake/intakes section
    return month_hits >= 2

def _amount_near_label(line: str, label_keys: List[str]) -> Optional[float]:
    if not line:
        return None
    l = line.lower()
    # take the last occurrence of any synonym on the line
    idxs = [l.rfind(k) for k in label_keys if k in l]
    if not idxs:
        return None
    idx = max(idxs)
    positions = _find_all_rm_amounts_with_pos(line)
    if not positions:
        return None
    # pick the RM value whose position is closest to the label token
    _, val = min(positions, key=lambda p: abs(p[0] - idx))
    return val


def _extract_fees_from_text(txt: str) -> Dict[str, Optional[float]]:
    """
    Return {'local': x, 'international': y} by matching the amount nearest to each label
    on the SAME line when possible. Includes robust fallbacks:
      - INTI CAL-style "X Subjects - RM Y": take the highest amount (e.g., 4 subjects).
      - If 'International' is mentioned but no distinct amount is found, set it = local.
    """
    out = {'local': None, 'international': None}
    if not txt:
        return out

    # --- Pre-normalize for flattened tables like "RM 21,0874 Subjects"
    txt = _normalize_subject_plan_text(txt)

    # --- Fast path: CAL-style subject plans (e.g., "3 Subjects - RM 21,087  4 Subjects - RM 28,116")
    plan_vals = _extract_subject_plan_amounts(txt)
    if plan_vals:
        # use the highest RM (e.g., 4-subject option) as the fee figure for both columns
        best = max(plan_vals)
        out['local'] = best
        # If the block mentions an international column at all, mirror local => international
        if re.search(r'(international|non[-\s]?malaysian|foreign|intl)', txt, flags=re.IGNORECASE):
            out['international'] = best
        return out

    # --- Standard label-based extraction (original logic, slightly hardened)
    local_labels = ['local', 'malaysian', 'malaysians', 'msian', 'domestic']
    intl_labels = ['international', 'non-malaysian', 'non malaysian', 'intl', 'foreign']

    def has_any(s: str, keys: list) -> bool:
        s = (s or '').lower()
        return any(k in s for k in keys)

    def _amount_near_label(line: str, label_keys: List[str]) -> Optional[float]:
        if not line:
            return None
        l = line.lower()
        idxs = [l.rfind(k) for k in label_keys if k in l]
        if not idxs:
            return None
        idx = max(idxs)
        positions = _find_all_rm_amounts_with_pos(line)
        if not positions:
            return None
        _, val = min(positions, key=lambda p: abs(p[0] - idx))
        return val

    # detect "local-only" disclaimer anywhere in the block
    local_only = bool(re.search(r'(local\s+students?\s+only|for\s+malaysian[s]?)', txt, flags=re.IGNORECASE))
    saw_intl_wording = bool(re.search(r'(international|non[-\s]?malaysian|foreign|intl)', txt, flags=re.IGNORECASE))

    # pass 1: line-by-line, pick amounts ONLY when the label appears on that line
    lines = [l.strip() for l in (txt.splitlines() or []) if l.strip()]
    for line in lines:
        low = line.lower()

        if out['local'] is None and has_any(low, local_labels):
            amt = _amount_near_label(line, local_labels)
            if amt is not None:
                out['local'] = amt

        if out['international'] is None and has_any(low, intl_labels):
            amt = _amount_near_label(line, intl_labels)
            if amt is not None:
                out['international'] = amt

        # Range like "RM20,000 - RM25,000" -> treat as a single local hint if international not on same line
        if out['local'] is None and not has_any(low, intl_labels):
            m = re.search(r'rm\s*([0-9][0-9,\.\s]*)\s*[-–—]\s*rm?\s*([0-9][0-9,\.\s]*)', line, flags=re.IGNORECASE)
            if m:
                try:
                    lo = float(re.sub(r'[\s,]', '', m.group(1)))
                    hi = float(re.sub(r'[\s,]', '', m.group(2)))
                    out['local'] = min(lo, hi)
                except Exception:
                    pass

    # pass 2: conservative fallbacks over the whole block
    amounts_block = [v for _, v in _find_all_rm_amounts_with_pos('\n'.join(lines))]

    # Local fallback: first RM amount if still missing
    if out['local'] is None and amounts_block:
        out['local'] = amounts_block[0]

    # International fallback:
    if out['international'] is None:
        if local_only:
            out['international'] = None
        elif saw_intl_wording:
            # If there's evidence of an "International" column but no distinct number,
            # mirror local (common on tables where both columns are equal).
            if out['local'] is not None:
                out['international'] = out['local']
            elif amounts_block:
                # best-effort: pick any different RM than the first, else mirror
                try:
                    lv = amounts_block[0]
                    cand = [a for a in amounts_block if a != lv]
                    out['international'] = cand[0] if cand else lv
                except Exception:
                    out['international'] = None

    return out


# ---------- Structured logging ----------
LOG_DIR = Path(os.getenv("LOG_DIR", "./logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
VERBOSE = os.getenv("RAG_VERBOSE", "0").lower() in ("1", "true", "yes", "on")

def _summary_path() -> Path:
    summary_dir = LOG_DIR / "log"
    summary_dir.mkdir(parents=True, exist_ok=True)
    return summary_dir / f"rag_{time.strftime('%Y-%m-%d')}.jsonl"

def _debug_path() -> Path:
    debug_dir = LOG_DIR / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    return debug_dir / f"rag_debug_{time.strftime('%Y-%m-%d')}.jsonl"

def log_summary(**fields):
    fields.setdefault("ts", time.strftime('%Y-%m-%dT%H:%M:%S%z'))
    line = json.dumps(fields, ensure_ascii=False)
    try:
        with open(_summary_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

_logger = logging.getLogger("rag_debug")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    if VERBOSE:
        fh = logging.FileHandler(_debug_path(), encoding="utf-8")
        fh.setFormatter(logging.Formatter('%(message)s'))
        _logger.addHandler(fh)
    else:
        _logger.addHandler(logging.NullHandler())

def log_event(event: str, **fields):
    if not VERBOSE:
        return
    payload = {
        "ts": time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        "event": event,
        **fields
    }
    try:
        _logger.info(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass

# === Feedback Storage ===
def _feedback_path() -> Path:
    # Ensure logs/feedback/ exists
    feedback_dir = LOG_DIR / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    return feedback_dir / f"feedback_{time.strftime('%Y-%m-%d')}.jsonl"

def save_feedback_line(**fields):
    fields.setdefault("ts", time.strftime('%Y-%m-%dT%H:%M:%S%z'))
    try:
        with open(_feedback_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except Exception:
        pass


class SmartRAGEngine:
    """ RAG engine — now powered by LlamaIndex for retrieval, same public API. """

    def __init__(self,
                 db_path: str = "./vector_db",
                 ollama_url: str = "http://localhost:11434",
                 model_name: str = "llama3.2:3b",
                 use_reranker: bool = True,
                 reranker_strategy: str = "cross-encoder",
                 reranker_model: Optional[str] = None):

        # --- Chroma setup
        self.client = chromadb.PersistentClient(
            path=db_path,
            settings=ChromaSettings(anonymized_telemetry=False)
        )

        collection_name = os.getenv("CHROMA_COLLECTION", "university_docs")

        # ✅ Always get the existing collection as-is.
        #    It already knows which embedding function was used at ingest.
        self.collection = self.client.get_collection(name=collection_name)

        # Use the embedding model recorded during ingest (set in scripts/ingest.py)
        stored = (self.collection.metadata or {}).get("embedding_model")
        query_model = stored or "all-MiniLM-L6-v2"
        self.embedding_fn = None  # we don't attach a new embedding_fn to Chroma

        # LlamaIndex vectors using existing Chroma collection
        self._vector_store = ChromaVectorStore(chroma_collection=self.collection)
        self._storage_context = StorageContext.from_defaults(vector_store=self._vector_store)

        # ✅ LlamaIndex embedding model that matches the stored vectors
        from sentence_transformers import SentenceTransformer

        class MiniLMEmbedding(BaseEmbedding):
            """LlamaIndex embedding wrapper for all-MiniLM-L6-v2."""

            @classmethod
            def class_name(cls) -> str:
                return "MiniLMEmbedding"

            def __init__(self, model_name: str):
                super().__init__(model_name=model_name)
                self._model = SentenceTransformer(model_name)

            def _get_text_embedding(self, text: str) -> List[float]:
                return self._model.encode(text, convert_to_numpy=False).tolist()

            async def _aget_text_embedding(self, text: str) -> List[float]:
                return self._get_text_embedding(text)

            def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
                return self._model.encode(texts, convert_to_numpy=False).tolist()

            async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
                return self._get_text_embeddings(texts)

            def _get_query_embedding(self, query: str) -> List[float]:
                return self._get_text_embedding(query)

            async def _aget_query_embedding(self, query: str) -> List[float]:
                return self._aget_text_embedding(query)

        # Use the same model name that was used during ingest
        LISettings.embed_model = MiniLMEmbedding(model_name=query_model)

        # Build index from the existing vector store (no re-ingest)
        self._index = VectorStoreIndex.from_vector_store(
            vector_store=self._vector_store,
            storage_context=self._storage_context,
        )

        # Ollama settings
        self.ollama_url = ollama_url
        self.model_name = model_name

        # Initialize Reranker (unchanged)
        self.use_reranker = use_reranker and RERANKER_AVAILABLE
        self.reranker: Optional[BaseReranker] = None
        if self.use_reranker:
            try:
                self.reranker = create_reranker(strategy=reranker_strategy, model_name=reranker_model)
                print(f"✓ Reranker enabled: {reranker_strategy}")
            except Exception as e:
                print(f"⚠ Failed to initialize reranker: {e}")
                self.use_reranker = False

        print(f"✓ Smart RAG Engine initialized (LlamaIndex retrieval)")
        print(f"  - Model: {model_name}")
        try:
            print(f"  - Documents: {self.collection.count()}")
        except Exception:
            print(f"  - Documents: (count unavailable)")
        print(f"  - Reranker: {'Enabled' if self.use_reranker else 'Disabled'}")
        try:
            print(f"  - Chroma collection: {collection_name}")
            print(
                f"  - Embedding model (ingest/query): {(self.collection.metadata or {}).get('embedding_model')} / {query_model}")
        except Exception:
            pass

        try:
            log_event(
                "engine_init",
                model=self.model_name,
                reranker=bool(self.use_reranker),
                docs=self.collection.count(),
                db_path=db_path
            )
        except Exception:
            pass

        # ---- Keep last answer metadata for feedback linking ----
        self._last_request_id: Optional[str] = None
        self._last_query: Optional[str] = None
        self._last_sources: List[Dict] = []
        self._last_university_filter: Optional[str] = None

        # ---- Conversation context for follow-up questions ----
        self._last_course_id: Optional[str] = None
        self._last_course_university: Optional[str] = None
        self._last_course_title: Optional[str] = None


    # ---------- Pretty headers ----------
    def _icon_for(self, label: str) -> str:
        mp = {
            'Programme Structure': '📘',
            'Fees': '💸', 'Fee & Intakes': '💸',
            'Campus Intakes': '📅', 'Intakes': '📅',
            'Entry Requirements': '🧾',
            'Duration': '⏳',
            'How to Apply': '📝',
            'Scholarships': '🎓',
            'Campus': '📍',
            'Programs': '📚',
            'Universities': '🏫',
            'Answer': '🧠',
        }
        return mp.get(label, '🧠')

    def _canon_section_label(self, section: str | None) -> str | None:
        if not section: return None
        s = section.strip().lower()
        norm = {
            'programme structure': 'Programme Structure',
            'program structure': 'Programme Structure',
            'structure': 'Programme Structure',
            'course structure': 'Programme Structure',
            'curriculum': 'Programme Structure',
            'modules': 'Programme Structure',
            'subjects': 'Programme Structure',
            'syllabus': 'Programme Structure',
            'fees': 'Fees', 'fee': 'Fees', 'tuition': 'Fees', 'fee & intakes': 'Fees',
            'intakes': 'Campus Intakes', 'campus intakes': 'Campus Intakes',
            'entry requirements': 'Entry Requirements',
            'entry requirement': 'Entry Requirements',
            'requirements': 'Entry Requirements',
            'duration': 'Duration', 'course duration': 'Duration', 'programme duration': 'Duration',
        }
        return norm.get(s, section)

    def _clean_program_name(self, s: str | None) -> str:
        import re
        if not s: return ''
        t = re.sub(r'\b(program(me)?|programme|course|degree)\b', '', s, flags=re.I)
        t = re.sub(r'\s+', ' ', t).strip()
        return t.title()

    def _pick_program_and_uni(
        self,
        query_info: Optional[dict],
        chunks: list[dict] | None
    ) -> tuple[str | None, str | None, str | None]:
        """
        Decide which programme + university to show in the title bar.

        Robust against query_info=None.
        """
        query_info = query_info or {}
        chunks = chunks or []

        prog: str | None = None
        uni_short: str | None = query_info.get('university') or None

        # Try to get course_id from filters or from chunks
        cid = query_info.get('course_id_filter')
        if not cid and chunks:
            cid = next(
                (
                    (c.get('metadata') or {}).get('course_id')
                    for c in chunks
                    if (c.get('metadata') or {}).get('course_id')
                ),
                None
            )

        # If no explicit university, try infer from chunks
        if chunks and not uni_short:
            uni_short = next(
                (
                    (c.get('metadata') or {}).get('university_short')
                    for c in chunks
                    if (c.get('metadata') or {}).get('university_short')
                ),
                None
            )

        title: str | None = None

        # Prefer the exact course title if we know the course_id
        if cid and chunks:
            title = next(
                (
                    (c.get('metadata') or {}).get('course_title')
                    for c in chunks
                    if (c.get('metadata') or {}).get('course_id') == cid
                    and (c.get('metadata') or {}).get('course_title')
                ),
                None
            )

        # Fall back to program_query.display if present
        pq = query_info.get('program_query') or {}
        if not title and isinstance(pq, dict) and pq.get('display'):
            title = pq['display']

        # As a last resort, derive from slug
        if not title and cid:
            title = cid.replace('-', ' ').title()

        prog = title or None
        uni_disp = _canonical_uni_display(uni_short) if uni_short else None
        return prog, uni_short, uni_disp

    def _build_title(
        self,
        query_info: Optional[dict],
        chunks: list[dict] | None = None,
        *,
        region: str | None = None
    ) -> str:
        """
        Build the H1 title used at the top of the answer.

        Robust against query_info=None.
        """
        query_info = query_info or {}
        chunks = chunks or []

        doc = (query_info.get('doc_type') or '').lower()
        is_list_unis = bool(query_info.get('list_universities'))
        is_list_courses = bool(
            query_info.get('course_query')
            and query_info.get('list_query')
            and not query_info.get('section')
        )
        is_offer_lookup = bool(query_info.get('offer_lookup'))
        sec = self._canon_section_label(query_info.get('section'))
        prog, uni_short, uni_disp = self._pick_program_and_uni(query_info, chunks)

        # --- List of universities (e.g. "universities in Penang") ---
        if is_list_unis:
            label = f"Universities in {region.title() if region else 'Penang'}"
            return f"{self._icon_for('Universities')} {label}"

        # --- List of programmes ---
        if is_list_courses:
            label = "Programs"
            if uni_disp:
                return f"{self._icon_for('Programs')} {label} at {uni_disp}"
            return f"{self._icon_for('Programs')} {label}"

        # --- "Which uni offers X?" style ---
        if is_offer_lookup:
            base = f"{self._icon_for('Programs')} Universities offering — "
            pq = query_info.get('program_query') or {}
            disp = None
            if isinstance(pq, dict):
                disp = pq.get('display')
            name = self._clean_program_name(prog or disp or '')
            if query_info.get('course_type_filter'):
                label = f"{query_info['course_type_filter'].title()} in {name}"
                return base + label
            return base + (name or "That Program")

        # --- Simple docs: scholarship / how-to-apply / campus ---
        if doc == 'scholarship':
            return f"{self._icon_for('Scholarships')} Scholarships" + (f" at {uni_disp}" if uni_disp else "")
        if doc == 'how_to_apply':
            return f"{self._icon_for('How to Apply')} How to Apply" + (f" at {uni_disp}" if uni_disp else "")
        if doc == 'campus':
            return f"{self._icon_for('Campus')} Campus" + (f" — {uni_disp}" if uni_disp else "")

        # --- Courses + specific section (structure / fees / duration / entry / intakes) ---
        if doc == 'courses' and sec:
            label = sec
            icon = self._icon_for(sec)
            prog_clean = self._clean_program_name(prog)
            tail = ""
            if prog_clean and uni_disp:
                tail = f" — {prog_clean} at {uni_disp}"
            elif prog_clean:
                tail = f" — {prog_clean}"
            elif uni_disp:
                tail = f" — {uni_disp}"
            return f"{icon} {label}{tail}"

        # --- Cheapest/fee comparison header ---
        if query_info.get('compare_fees'):
            pq = query_info.get('program_query') or {}
            disp = None
            if isinstance(pq, dict):
                disp = pq.get('display')
            name = self._clean_program_name(prog or disp or '')
            if name:
                return f"{self._icon_for('Fees')} Fees — {name}"
            return f"{self._icon_for('Fees')} Fees"

        # --- Default ---
        return f"{self._icon_for('Answer')} Answer"

    def _emit_via_llm(self, text: str, stream: bool = True, title: str | None = None):
        """Echo `text` with a single big H1 title (provided), then the body."""
        import re
        t = (title or "Answer").strip()
        safe_text = _normalize_uni_mentions(text)
        body = f"# {t}\n\n{safe_text}"
        body = _fix_heading_runs(body)

        prompt = f"Return EXACTLY the following text without adding anything else:\n\n{body}"
        try:
            if stream:
                response = ollama.chat(
                    model=self.model_name,
                    messages=[{'role': 'user', 'content': prompt}],
                    stream=True,
                    keep_alive=LLM_KEEP_ALIVE,
                    options=OLLAMA_OPTIONS
                )
                for chunk in response:
                    msg = chunk.get('message', {}).get('content')
                    if msg:
                        # fix any merged headings that slipped through
                        yield _fix_heading_runs(msg)
            else:
                response = ollama.chat(
                    model=self.model_name,
                    messages=[{'role': 'user', 'content': prompt}],
                    stream=False,
                    keep_alive=LLM_KEEP_ALIVE,
                    options=OLLAMA_OPTIONS
                )
                yield _fix_heading_runs(response['message']['content'])
        except Exception:
            # last-resort fallback so the UI still gets something
            yield _fix_heading_runs(body)

    def _echo_via_llm_segmented(self, text: str, title: str = None, stream: bool = True):
        import re
        t = (self._last_query or "Answer")
        t = re.sub(r'\s+', ' ', t).strip().rstrip('?') or "Answer"
        if title:
            t = title.strip()

        # Title once
        header = f"# {t}\n\n"
        yield _fix_heading_runs(header)

        segments = _split_for_llm(text, ECHO_SEGMENT_CHARS) or [text]

        for idx, seg in enumerate(segments):
            seg = _fix_heading_runs(seg)
            prompt = (
                "You are a copier. Return EXACTLY the text between <BEGIN> and <END>. "
                "Do not add or remove anything. No code fences.\n"
                "<BEGIN>\n" + seg + "\n<END>"
            )
            dyn_predict = max(_approx_tokens(len(seg)), int(OLLAMA_OPTIONS.get("num_predict", 384)))
            options = dict(OLLAMA_OPTIONS)
            options["num_predict"] = dyn_predict

            try:
                if stream:
                    resp = ollama.chat(
                        model=self.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        stream=True,
                        keep_alive=LLM_KEEP_ALIVE,
                        options=options,
                    )
                    for chunk in resp:
                        msg = chunk.get("message", {}).get("content")
                        if msg:
                            yield _fix_heading_runs(msg)
                else:
                    resp = ollama.chat(
                        model=self.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        stream=False,
                        keep_alive=LLM_KEEP_ALIVE,
                        options=options,
                    )
                    yield _fix_heading_runs(resp["message"]["content"])
            except Exception:
                # last-resort fallback
                yield _fix_heading_runs(seg)

            # re-insert the paragraph break that was removed by _split_for_llm
            if idx < len(segments) - 1:
                yield "\n\n"

    def _infer_course_type_from_title(self, title: str):
        n = (title or "").lower()
        import re

        # --- Master FIRST (so "MEng 4+0" doesn't get swallowed by degree rules)
        if re.search(r'\bmaster\b|\bm\.?(?:sc|eng)\b|\bmba\b|\bllm\b|\bll\.?m\b|master of laws', n):
            return "master"

        # --- Degree-transfer programmes (e.g., "UK Degree Transfer Programme (Law)")
        # Treat these as Degree unless they explicitly look like ADP/ADTP/1+3 etc.
        if re.search(r'\bdegree\s+transfer\b', n) and not re.search(
                r'\b(adp|adtp|american\s+degree|[12]\s*\+\s*[123])\b', n
        ):
            return "degree"

        # --- American University (degree unless clearly transfer/ADTP)
        if re.search(r'american university', n) and not re.search(
                r'\b(adp|adtp|transfer|credit\s*transfer|[12]\s*\+\s*[123])\b', n
        ):
            return "degree"

        # Foundation/Pre-U
        if re.search(r'foundation|pre[-\s]?u|pre[-\s]?university|\ba[-\s]?levels?\b|\bcal\b', n):
            return "foundation"

        # Diploma
        if re.search(r'\bdiploma\b', n):
            return "diploma"

        # Degree (Bachelor-level + common signals)
        if re.search(r'\bbachelor\b|\bb\.(?:sc|a|eng)\b|\bbsc\b|\bba\b|\bbeng\b|\bhon(?:ours|s)?\b|\b3\+?0\b|\b4\+?0\b',
                     n):
            return "degree"

        # Extra Degree markers for Law (LLB etc.)
        if re.search(r'\bllb\b|\bll\.?\s*b\b|\bbachelor\s+of\s+law', n):
            return "degree"

        # PhD / Doctoral (incl. “Doctor of …”)
        if re.search(r'\bph\.?d\b|doctor(?:al|ate)?|doctor of philosophy', n):
            return "phd"

        # Certificate
        if re.search(r'\bcertificate\b|\bcert\b', n):
            return "certificate"

        return None

    def _course_type_matches(self, meta_type: Optional[str], desired: Optional[str]) -> bool:
        """
        Return True if a stored course_type value should be treated as the requested level
        (e.g. 'Foundation in Arts' should match 'foundation').

        Uses COURSE_TYPE_SYNONYMS plus some loose substring checks.
        """
        if not desired:
            return True
        if not meta_type:
            return False

        mt = (meta_type or "").strip().lower()
        want = (desired or "").strip().lower()

        # Exact / synonym match first
        syns = COURSE_TYPE_SYNONYMS.get(want, [desired])
        syns_lower = {s.lower() for s in syns}
        if mt in syns_lower:
            return True

        # Loose heuristics by family
        if want == "foundation":
            return (
                "foundation" in mt
                or "pre-u" in mt
                or "pre u" in mt
                or "pre-university" in mt
                or "pre university" in mt
                or "a-level" in mt
                or "a level" in mt
                or "cal" in mt
            )

        if want == "diploma":
            return "diploma" in mt

        if want == "certificate":
            return "certificate" in mt or mt.startswith("cert ")

        if want == "degree":
            return (
                "bachelor" in mt
                or "degree" in mt
                or "honours" in mt
                or "honors" in mt
                or "hons" in mt
            )

        if want == "master":
            return (
                "master" in mt
                or "msc" in mt
                or "m.sc" in mt
                or "mba" in mt
                or "meng" in mt
                or "m.eng" in mt
            )

        if want == "phd":
            return (
                "phd" in mt
                or "ph.d" in mt
                or "doctor" in mt
                or "doctoral" in mt
                or "doctorate" in mt
            )

        return False

    # ===================== Intent detection =====================
    def detect_query_type(self, query: str) -> Dict:
        query_lower = query.lower()

        result: Dict = {
            'university': None,
            'doc_type': None,
            'course_query': False,
            'section': None,
            'list_query': False,
            'course_type_filter': None,
            'degree_subtype': None,
            'list_universities': False,
            'offer_lookup': False,
            'compare_fees': False,
            'program_query': None,     # canonical field used elsewhere
            'program_keyword': None,   # kept for backwards compatibility
        }

        # ----- Region / global “universities in Penang” style queries -----
        if 'penang' in query_lower or 'pulau pinang' in query_lower:
            if any(k in query_lower for k in ['university', 'universities', 'uni', 'school', 'college']):
                # treat as a “show me all unis” style query even if “list” isn’t present
                result['list_universities'] = True

        # ----- University detection -----
        if 'inti' in query_lower:
            result['university'] = 'INTI'
        elif 'atc' in query_lower or 'advance tertiary' in query_lower:
            result['university'] = 'ATC'
        elif 'uow' in query_lower or 'wollongong' in query_lower:
            result['university'] = 'UOW'
        elif 'msu' in query_lower or 'management science' in query_lower:
            result['university'] = 'MSU'
        elif 'mckl' in query_lower or 'methodist college kuala lumpur' in query_lower:
            result['university'] = 'MCKL'
        elif 'pidc' in query_lower or 'penang international dental college' in query_lower:
            result['university'] = 'PIDC'
        elif 'sentral' in query_lower or 'sentral college' in query_lower:
            result['university'] = 'SENTRAL'
        elif 'toa' in query_lower or 'the one academy' in query_lower:
            result['university'] = 'TOA'
        elif 'unitar' in query_lower or 'unitar college' in query_lower:
            result['university'] = 'UNITAR'
        elif 'psdc' in query_lower or 'penang skills development centre' in query_lower:
            result['university'] = 'PSDC'
        elif 'royal' in query_lower or 'royal college' in query_lower:
            result['university'] = 'ROYAL'
        elif 'veritas' in query_lower or 'veritas college' in query_lower:
            result['university'] = 'VERITAS'
        elif 'taru' in query_lower or 'tunku abdul rahman university' in query_lower:
            result['university'] = 'TARU'
        elif 'peninsula' in query_lower or 'peninsula college' in query_lower:
            result['university'] = 'PENINSULA'

        # ----- “Which uni offers X?” (offer lookup) -----
        offer_verbs = ('offer', 'offers', 'provide', 'provides', 'have', 'has')
        if any(v in query_lower for v in offer_verbs):
            if 'which' in query_lower or any(k in query_lower for k in (
                'uni', 'university', 'college', 'school',
                'program', 'programme', 'course', 'courses', 'degree'
            )):
                # Intention: they’re asking about programmes, not just generic info
                result['doc_type'] = result.get('doc_type') or 'courses'
                result['course_query'] = True
                result['list_query'] = True       # default to “list programmes”
                result['offer_lookup'] = True     # but allow special handling if a real programme phrase is present

                program_phrase = self._extract_program_query(query_lower)
                result['program_query'] = program_phrase
                result['program_keyword'] = program_phrase

                # Guard: if the extracted phrase is too generic (“what programmes”, “any course”, etc.),
                # then treat it as a plain list query instead of specific offer lookup
                if self._is_generic_program_query(program_phrase):
                    result['offer_lookup'] = False

        # ----- Doc type: how to apply / scholarship / campus / courses -----
        if any(word in query_lower for word in ['apply', 'admission', 'register', 'enrol', 'enroll']):
            result['doc_type'] = 'how_to_apply'

        elif any(word in query_lower for word in [
            'scholarship', 'scholarships', 'scholar', 'scolarship',
            'financial aid', 'financial assistance', 'aid', 'bursary', 'award',
            'ptptn', 'edu-assist', 'edu assist', 'study loan', 'loan'
        ]):
            result['doc_type'] = 'scholarship'

        elif any(word in query_lower for word in ['campus', 'location', 'address', 'where is']):
            result['doc_type'] = 'campus'

        elif (
            any(word in query_lower for word in [
                'course', 'courses', 'program', 'programme', 'programs', 'programmes', 'study', 'studies',
                'subject', 'subjects', 'module', 'modules', 'curriculum', 'syllabus',
                'diploma', 'degree', 'bachelor', 'certificate',
                'foundation', 'pre-university', 'pre university', 'pre-u', 'pre u', 'pre-university'
            ])
            or any(word in query_lower for word in ['fee', 'fees', 'tuition', 'cost', 'price'])
        ):
            result['doc_type'] = 'courses'
            result['course_query'] = True

        # ----- Direct “structure” phrase outside course_query block -----
        if any(word in query_lower for word in ['structure', 'curriculum', 'subject', 'subjects', 'module', 'modules', 'syllabus']):
            result['section'] = 'Programme Structure'

        # ----- “Cheapest / compare fees” queries -----
        fee_compare_terms = (
            'cheapest', 'lowest', 'most affordable', 'affordable',
            'low fee', 'lower fee', 'cheap', 'budget', 'least expensive'
        )
        if any(t in query_lower for t in fee_compare_terms) or re.search(r'\b(top\s*3|3\s*cheapest)\b', query_lower):
            result['doc_type'] = 'courses'
            result['course_query'] = True
            result['section'] = 'Fees'
            result['list_query'] = False
            result['compare_fees'] = True

            program_phrase = self._extract_program_query(query_lower)
            result['program_query'] = program_phrase
            result['program_keyword'] = program_phrase

        # ----- Course-specific refinements -----
        if result['course_query']:
            # Generic list / availability wording
            if any(word in query_lower for word in ['offer', 'offers', 'list', 'available', 'have', 'has']):
                result['list_query'] = True

            generic_list_patterns = [
                'what are the programs', 'what are the programmes',
                'what is the program', 'what is the programme',
                'program list', 'programme list',
                'list of programs', 'list of programmes',
                'what courses', 'courses offered', 'programs offered', 'programmes offered'
            ]
            if any(p in query_lower for p in generic_list_patterns):
                result['list_query'] = True

            # If a specific university is mentioned + generic “program/courses”
            # and NO section keywords were detected, assume they want a list.
            if result.get('university') and not result.get('section'):
                if any(k in query_lower for k in ['program', 'programme', 'programs', 'programmes', 'courses']):
                    result['list_query'] = True

            # Course level filters
            if any(word in query_lower for word in ['foundation', 'pre-university', 'pre university', 'pre-u', 'pre u']):
                result['course_type_filter'] = 'foundation'
            elif any(word in query_lower for word in ['degree', 'bachelor']):
                result['course_type_filter'] = 'degree'
            elif 'diploma' in query_lower:
                result['course_type_filter'] = 'diploma'
            elif 'certificate' in query_lower:
                result['course_type_filter'] = 'certificate'

            # Degree subtype
            if any(word in query_lower for word in ['doctor', 'phd', 'doctoral', 'doctorate']):
                result['degree_subtype'] = 'doctor'
            elif any(word in query_lower for word in ['masters', 'master', 'msc', 'm.sc', 'mba']):
                result['degree_subtype'] = 'master'
            elif 'bachelor' in query_lower:
                result['degree_subtype'] = 'bachelor'

            # Section targeting inside course queries
            if any(word in query_lower for word in ['structure', 'curriculum', 'subject', 'subjects', 'module', 'modules', 'learn', 'syllabus']):
                result['section'] = 'Programme Structure'
            elif any(word in query_lower for word in ['fee', 'fees', 'cost', 'price', 'tuition']):
                result['section'] = 'Fees'
            elif any(word in query_lower for word in ['intake', 'intakes', 'start']):
                result['section'] = 'Campus Intakes'
            elif any(word in query_lower for word in [
                'entry', 'requirement', 'requirements', 'qualification', 'eligibility', 'spm', 'stpm', 'a-level', 'alevel'
            ]):
                result['section'] = 'Entry Requirements'
            elif any(word in query_lower for word in ['duration', 'how long', 'length of study', 'length']):
                result['section'] = 'Duration'

        # ----- Scholarship list queries -----
        if result['doc_type'] == 'scholarship' and not result['course_query']:
            if any(word in query_lower for word in ['list', 'lists', 'offer', 'offers', 'available', 'types', 'all']):
                result['list_query'] = True

        # ----- Fallbacks: entry / duration queries WITHOUT explicit "course" keywords -----
        # e.g. "what is the entry requirement for Cambridge A Level CAL in inti"
        if not result['doc_type'] and 'entry requirement' in query_lower:
             result['doc_type'] = 'courses'
             result['course_query'] = True
             result['section'] = 'Entry Requirements'

        if not result['doc_type'] and any(p in query_lower for p in [
              'duration', 'how long', 'length of study', 'length of programme', 'length of program'
        ]):
               result['doc_type'] = 'courses'
               result['course_query'] = True
               result['section'] = 'Duration'

        # ----- Fallbacks: subject / intake queries without explicit course words -----
        if not result['doc_type'] and any(word in query_lower for word in
                                          ['subject', 'subjects', 'module', 'modules', 'curriculum', 'syllabus',
                                           'structure']):
                result['doc_type'] = 'courses'
                result['course_query'] = True
                result['section'] = 'Programme Structure'

        if not result['doc_type'] and any(word in query_lower for word in ['intake', 'intakes', 'start']):
               result['doc_type'] = 'courses'
               result['course_query'] = True
               result['section'] = 'Campus Intakes'

        return result

    # ===================== Retrieval (now via LlamaIndex) =====================
    def _li_where_to_metadata_filter(self, query_info: Dict) -> Dict:
        """Build a simple metadata filter dict compatible with LlamaIndex retriever."""
        filters = {}

        if query_info.get('university'):
            filters['university_short'] = query_info['university']

        if query_info.get('doc_type'):
            filters['document_type'] = query_info['doc_type']

        if query_info.get('course_id_filter'):
            filters['course_id'] = query_info['course_id_filter']

        if query_info.get('course_type_filter'):
            filters['course_type'] = query_info['course_type_filter']

        # Section synonyms handled later in post-filtering; keep a direct hint if exact found
        if query_info.get('section'):
            filters['section_hint'] = query_info['section']  # not guaranteed in data

        return filters

    def _to_li_filters(self, q: Dict) -> Optional[MetadataFilters]:
        fs = []
        if q.get('university'):
            fs.append(ExactMatchFilter(key="university_short", value=q['university']))
        if q.get('doc_type'):
            fs.append(ExactMatchFilter(key="document_type", value=q['doc_type']))
        if q.get('course_id_filter'):
            fs.append(ExactMatchFilter(key="course_id", value=q['course_id_filter']))
        # elif q.get('course_type_filter'):
        # fs.append(ExactMatchFilter(key="course_type", value=q['course_type_filter']))
        return MetadataFilters(filters=fs) if fs else None

    def _li_nodes_to_context(self, nodes: List[NodeWithScore]) -> List[Dict]:
        """Convert LI nodes to existing context chunk shape."""
        context = []
        for n in nodes:
            meta = dict(n.node.metadata or {})
            # score can be None; LlamaIndex gives higher better (similarity)
            sim = float(n.score) if n.score is not None else 0.0
            # clamp 0..1 if needed
            if sim < 0.0: sim = 0.0
            if sim > 1.0: sim = 1.0
            context.append({
                'content': n.node.get_content(metadata_mode="none"),
                'metadata': meta,
                'relevance_score': sim
            })
        # stable sort
        context.sort(key=lambda c: (-float(c.get('relevance_score', 0.0)), _stable_tie_key(c.get('metadata'))))
        return context

    def retrieve_context(self, query: str, query_info: Dict, n_results: int = 3, request_id: Optional[str] = None) -> \
    List[Dict]:
        """Retrieve candidate chunks via LlamaIndex retriever (uses your existing Chroma data)."""
        # Over-fetch if reranker is on (so it has room to work)
        retrieval_k = n_results
        if self.use_reranker:
            retrieval_k = max(n_results * 4, 12)

        # LlamaIndex retriever with a metadata prefilter (best-effort)
        li_filters = self._to_li_filters(query_info)
        retriever = self._index.as_retriever(
            similarity_top_k=retrieval_k,
            filters=li_filters
        )

        log_event("retrieve_start", request_id=request_id, n_results=n_results,
                  where=self._li_where_to_metadata_filter(query_info))
        results: List[NodeWithScore] = retriever.retrieve(query)

        # Convert to our shape
        context_chunks = self._li_nodes_to_context(results)

        # ---- Apply stricter filtering (section synonyms etc.), emulating your previous Chroma where
        def section_ok(meta_sec: Optional[str], desired: Optional[str], txt: str = "") -> bool:
            if not desired:
                return True
            synonyms_map = {
                'Programme Structure': [
                    'Programme Structure', 'Program Structure', 'Structure', 'Course Structure',
                    'Curriculum', 'Modules', 'Subjects', 'Syllabus'
                ],
                'Fees': [
                    'Fees', 'Fee', 'Fee & Intakes', 'Tuition'
                ],
                'Fee & Intakes': [
                    'Fee & Intakes', 'Fees', 'Fee', 'Intakes', 'Campus Intakes', 'Campus Intake'
                ],
                'Entry Requirements': [
                    'Entry Requirements', 'Requirements', 'Entry Requirement'
                ],
                # NEW: treat "Campus Intake" / "Intake" as the same family
                'Campus Intakes': [
                    'Campus Intakes', 'Campus Intake', 'Intakes', 'Intake', 'Fee & Intakes'
                ],
                'Intakes': [
                    'Intakes', 'Intake', 'Campus Intakes', 'Campus Intake', 'Fee & Intakes'
                ],
                'Core': [
                    'Core', 'Programme Structure', 'Program Structure', 'Structure', 'Course Structure',
                    'Curriculum', 'Modules', 'Subjects', 'Syllabus'
                ],
                'Electives': [
                    'Electives', 'Elective', 'Optional', 'Optional Modules'
                ]
            }

            if (meta_sec or '') in synonyms_map.get(desired, [desired]):
                return True
            if desired == 'Programme Structure' and _looks_like_structure_text(txt):
                return True
            if desired == 'Fees' and _looks_like_fees_text(txt):
                return True
            if desired == 'Entry Requirements' and _looks_like_entry_text(txt):
                return True
            if desired == 'Campus Intakes' and _looks_like_intakes_text(txt):
                return True
            return False

        strict = []
        for c in context_chunks:
            m = c.get('metadata', {}) or {}
            ok = True
            if query_info.get('university') and m.get('university_short') != query_info['university']:
                ok = False
            if ok and query_info.get('doc_type') and m.get('document_type') != query_info['doc_type']:
                ok = False
            if ok and query_info.get('course_id_filter') and m.get('course_id') != query_info['course_id_filter']:
                ok = False
            if ok and query_info.get('course_type_filter') and not query_info.get('course_id_filter'):
                if not self._course_type_matches(m.get('course_type'), query_info['course_type_filter']):
                    ok = False
            txt = c.get('content') or c.get('text') or c.get('document') or ''
            if ok and query_info.get('section') and not section_ok(m.get('section'), query_info.get('section'), txt):
                ok = False
            if ok:
                strict.append(c)

        # Are we asking for a very specific "this course + this section"?
        targeted_course = bool(query_info.get('course_id_filter'))
        targeted_section = (query_info.get('section') or '') in (
            'Entry Requirements',
            'Campus Intakes',
            'Duration',
            'Fees',
            'Fee & Intakes',
        )

        if strict:
            context_chunks = strict
        else:
            # For specific course + section questions, do NOT fall back to other courses.
            # It's better to return "I don't have that information." than a wrong answer.
            if targeted_course and targeted_section:
                context_chunks = []
            else:
                # relax section/course_id but NEVER relax university/doc_type
                relaxed = []
                for c in context_chunks:
                    m = c.get('metadata', {}) or {}
                    if query_info.get('university') and m.get('university_short') != query_info['university']:
                        continue
                    if query_info.get('doc_type') and m.get('document_type') != query_info['doc_type']:
                        continue
                    relaxed.append(c)
                context_chunks = relaxed or context_chunks

        log_event("retrieve_done", request_id=request_id, fetched=len(context_chunks),
                  use_reranker=bool(self.use_reranker))

        # ---------- Rerank (unchanged) ----------
        before_count = len(context_chunks)
        if self.use_reranker and self.reranker and before_count >= 2:
            context_chunks = self.reranker.rerank(
                query=query,
                documents=context_chunks,
                top_k=len(context_chunks)
            )
            log_event(
                "rerank_done",
                request_id=request_id,
                strategy=type(self.reranker).__name__ if self.reranker else None,
                top_k=len(context_chunks),
                before=before_count,
                after=len(context_chunks)
            )

        def _primary_score(c: Dict) -> float:
            if c.get('rerank_score') is not None:
                return float(c['rerank_score'])
            return float(c.get('relevance_score', 0.0))

        context_chunks.sort(key=lambda c: (-_primary_score(c), _stable_tie_key(c.get('metadata'))))
        final_chunks = context_chunks[:n_results]
        try:
            top = final_chunks[0]['relevance_score'] if final_chunks else None
            log_event("retrieve_final", request_id=request_id, returned=len(final_chunks), top_relevance=top)
        except Exception:
            pass
        return final_chunks

    # ===================== Course matching & listing (kept, uses Chroma directly) =====================
    def _match_course(self, query: str, university_short: Optional[str]) -> Optional[Dict[str, str]]:
        """
        Try to identify a single course from the user's query.

        Steps:
        1) Use _extract_program_query() to isolate the "program phrase" (e.g. 'actuarial-science-american-university in inti').
        2) Build a slug from that phrase and try to match directly against course_id / title slugs.
        3) If slug-based match fails, fall back to the original token-overlap scoring.
        """
        # --- 0) Fetch all course metadata for this uni (or all unis if uni not given) ---
        conditions = [{'document_type': 'courses'}]
        if university_short:
            conditions.append({'university_short': university_short})
        if len(conditions) == 1:
            where_clause = conditions[0]
        else:
            where_clause = {'$and': conditions}

        items = self.collection.get(where=where_clause, include=['metadatas'])
        metadatas = items.get('metadatas', [])
        if not metadatas:
            return None

        # --- 1) Program phrase + slug from the query (e.g. 'actuarial-science-american-university') ---
        pq = self._extract_program_query(query.lower())
        raw_phrase = (pq.get("raw") or "").strip()
        phrase_slug = _slugify(raw_phrase) if raw_phrase else ""

        # Also keep a "loose" slug directly from the whole query as a fallback
        loose_slug = _slugify(query)

        def _slug_candidates(meta: Dict) -> list[str]:
            cid = (meta.get('course_id') or '').strip()
            title = (meta.get('course_title') or '').strip()
            slugs = []
            if cid:
                slugs.append(cid.lower())
            if title:
                slugs.append(_slugify(title))
            return [s for s in slugs if s]

        # --- 2) Strongest: exact/substring slug match ---
        if phrase_slug:
            for meta in metadatas:
                for s in _slug_candidates(meta):
                    if phrase_slug == s or phrase_slug in s or s in phrase_slug:
                        return {
                            'course_id': meta.get('course_id'),
                            'course_title': meta.get('course_title') or (meta.get('course_id') or '').replace('-', ' ').title()
                        }

        # --- 3) Secondary: try using loose_slug (full query) against course_id/title slugs ---
        if loose_slug:
            for meta in metadatas:
                for s in _slug_candidates(meta):
                    if s and s in loose_slug:
                        return {
                            'course_id': meta.get('course_id'),
                            'course_title': meta.get('course_title') or (meta.get('course_id') or '').replace('-', ' ').title()
                        }

        # --- 4) Fallback: original token-overlap heuristic ---
        q = query.lower()
        tokens = re.findall(r"[a-zA-Z0-9]+", q)
        stop = {
            'what', 'is', 'the', 'program', 'programme', 'course', 'structure', 'fee', 'fees', 'intakes',
            'entry', 'requirements', 'requirement', 'about', 'and', 'of', 'in', 'at', 'for', 'name'
        }
        q_tokens = [t for t in tokens if t not in stop and len(t) > 2]

        best = None
        best_score = 0
        for meta in metadatas:
            cid = (meta.get('course_id') or '').lower()
            title = (meta.get('course_title') or '').lower()
            base = title if title else cid.replace('-', ' ')
            c_tokens = re.findall(r"[a-zA-Z0-9]+", base)
            c_tokens = [t for t in c_tokens if t not in stop and len(t) > 2]
            if not c_tokens:
                continue

            overlap = len(set(q_tokens) & set(c_tokens))
            if base and any(w in base for w in q_tokens):
                overlap += 1

            if overlap > best_score:
                best_score = overlap
                best = {
                    'course_id': meta.get('course_id'),
                    'course_title': meta.get('course_title') or cid.replace('-', ' ').title()
                }

        if best and best_score >= 1:
            return best
        return None

    def list_universities(self, region: Optional[str] = None) -> List[str]:
        """
        Return unique university names from the index. If region is provided (e.g., 'penang'),
        we keep only those whose metadata/text mention that region (best-effort).
        """
        try:
            items = self.collection.get(include=['documents', 'metadatas'])
        except Exception:
            return []

        docs = items.get('documents', []) or []
        metas = items.get('metadatas', []) or []

        found: Dict[str, str] = {}  # key: short/name, value: display name
        region_l = (region or '').lower().strip()

        for d, m in zip(docs, metas):
            m = m or {}
            short = (m.get('university_short') or '').strip()
            # Prefer a verbose name if you stored one; fall back to short code.
            name = (m.get('university_name') or m.get('university') or short or '').strip()
            if not name:
                continue

            # Build a best-effort haystack for region filtering
            meta_text_bits = []
            for k in ('state', 'city', 'address', 'location'):
                v = m.get(k)
                if isinstance(v, str):
                    meta_text_bits.append(v)
            haystack = f"{d or ''} {' '.join(meta_text_bits)}".lower()

            if region_l:
                if (region_l not in haystack) and (region_l not in name.lower()):
                    # also accept 'pulau pinang' as a synonym for 'penang'
                    if not (region_l == 'penang' and 'pulau pinang' in haystack):
                        continue

            found[name or short] = name

        # Return a sorted, de-duplicated list of display names
        return sorted({v for v in found.values() if v})

    def list_courses(self, query_info: Dict) -> List[str]:
        conditions = [{'document_type': 'courses'}]
        if query_info.get('university'):
            conditions.append({'university_short': query_info['university']})

        # Build strict where (with course_type if present)
        apply_type_filter = True
        if query_info.get('course_type_filter') == 'degree' and query_info.get('degree_subtype') in ('master',
                                                                                                     'doctor'):
            apply_type_filter = False

        strict_conditions = list(conditions)
        if apply_type_filter and query_info.get('course_type_filter'):
            ct = query_info['course_type_filter'].lower()
            ct_vals = COURSE_TYPE_SYNONYMS.get(ct, [query_info['course_type_filter']])
            strict_conditions.append({'$or': [{'course_type': v} for v in ct_vals]})

        def _where(conds):
            return conds[0] if len(conds) == 1 else {'$and': conds}

        try:
            # Strict pass
            items = self.collection.get(where=_where(strict_conditions), include=['metadatas'])
            metas = items.get('metadatas', []) or []

            # Fallback without course_type filter when nothing returned
            if not metas and (apply_type_filter and query_info.get('course_type_filter')):
                items = self.collection.get(where=_where(conditions), include=['metadatas'])
                metas = items.get('metadatas', []) or []

            # Build titles
            titles = []
            for meta in metas:
                t = self._course_title_from_meta(meta)
                if t:
                    titles.append(t)

            # If a level was requested, filter titles by inferred level (covers missing metadata)
            if apply_type_filter and query_info.get('course_type_filter'):
                want = query_info['course_type_filter'].lower()
                want_set = set(COURSE_TYPE_SYNONYMS.get(want, [want]))
                titles = [t for t in titles if (self._infer_course_type_from_title(t) or '') in want_set]

            # Degree subtypes (same as before)
            subtype = query_info.get('degree_subtype')
            if query_info.get('course_type_filter') in (None, 'degree') and subtype:
                def match_subtype2(name: str, st: str) -> bool:
                    n = name.lower()
                    if st == 'doctor':
                        return any(k in n for k in ['doctor', 'phd', 'ph.d', 'doctoral', 'doctorate'])
                    if st == 'master':
                        return any(k in n for k in ['master', 'msc', 'm.sc', 'mba']) and not any(
                            k in n for k in ['bachelor'])
                    if st == 'bachelor':
                        return 'bachelor' in n or any(k in n for k in ['b.sc', 'bsc', 'b.eng', 'beng', 'b.a', 'ba'])
                    return True

                titles = [t for t in titles if match_subtype2(t, subtype)]

            # de-dupe + sort
            seen, out = set(), []
            for t in titles:
                if t not in seen:
                    seen.add(t);
                    out.append(t)
            return out
        except Exception:
            return []

    def _categorize_courses_by_level(self, titles: List[str]) -> Dict[str, List[str]]:
        order = [
            "Foundation & Pre-U",
            "Certificate",
            "Diploma",
            "Degree",
            "Master",
            "Phd",
            "Pathway / Other"
        ]
        groups: Dict[str, List[str]] = {k: [] for k in order}

        for t in titles:
            n = (t or "").lower().strip()
            import re

            def has(*patterns) -> bool:
                return any(re.search(p, n, flags=re.IGNORECASE) for p in patterns)

            # Foundation & Pre-U
            if has(r'foundation', r'pre[-\s]?university', r'\ba[-\s]?level(s)?\b', r'\bcal\b'):
                groups["Foundation & Pre-U"].append(t)

            # Certificate
            elif has(r'\bcertificate\b', r'\bcert\b'):
                groups["Certificate"].append(t)

            # Diploma
            elif has(r'\bdiploma\b'):
                groups["Diploma"].append(t)

            # Master BEFORE Degree
            elif has(
                    r'\bmaster\b',
                    r'\bm\.?sc\b',
                    r'\bmeng\b',
                    r'\bm\.?eng\b',
                    r'\bmba\b',
                    r'\bllm\b',
                    r'\bll\.?m\b',
                    r'master of laws'
            ):
                groups["Master"].append(t)


            # PhD / Doctor (Doctor of ..., PhD, Doctorate)
            elif has(r'\bph\.?d\b', r'\bdoctor(?:al|ate)?\b', r'doctor of philosophy', r'^\s*doctor\b'):
                groups["Phd"].append(t)

            # Degree (Bachelor-level; plus Degree Transfer / LLB / UoL LLB)
            elif has(
                    r'\bbachelor\b',
                    r'\bb\.?sc\b', r'\bbsc\b',
                    r'\bb\.?a\b(?!\s*m)', r'\bba\b(?!\s*m)',
                    r'\bbeng\b', r'\bb\.?eng\b',
                    r'\bhon(?:ours|s)?\b',
                    r'\b3\+?0\b', r'\b4\+?0\b',
                    r'\bdegree\s+transfer\b',  # <— NEW
                    r'\bllb\b|\bll\.?\s*b\b|\bbachelor\s+of\s+law'  # <— NEW
            ) or (
                    # Treat "American University" as Degree unless clearly a transfer/ADTP
                    has(r'american university') and not has(
                r'\b(adp|adtp|transfer|credit\s*transfer|[12]\s*\+\s*[123])\b')
            ):
                groups["Degree"].append(t)

            # Pathway / Other — only if clearly pathway/transfer patterns found
            elif has(r'pathway') or has(r'\b(adp|adtp)\b') or has(r'\btransfer\b') or has(r'[12]\s*\+\s*[123]'):
                groups["Pathway / Other"].append(t)

            else:
                groups["Pathway / Other"].append(t)

        # Sort items and drop empty groups
        for k in groups:
            groups[k] = sorted(set(groups[k]), key=str.lower)
        return {k: v for k, v in groups.items() if v}

    def _is_level_header(self, text: str) -> bool:
        n = (text or "").lower()
        import re
        patterns = [
            r'foundation', r'pre[-\s]?u', r'pre[-\s]?university',
            r'\bcertificate\b',
            r'\bdiploma\b',
            r'\bbachelor\b|\bhon(?:ours|s)\b|\bb\.(?:sc|a|eng)\b|\bbsc\b|\bba\b|\bbeng\b',
            r'\bmaster\b|\bm\.?(sc|eng)\b|\bmba\b',
            r'\bph\.?d\b|\bdoctor(?:al|ate)?\b|doctor of philosophy',
            r'pathway|american university',
            r'\bpostgraduate\b'  # NEW
        ]
        return any(re.search(p, n, flags=re.IGNORECASE) for p in patterns)

    def _looks_like_course_title(self, text: str) -> bool:
        """
        Heuristic to decide whether a line from Courses.md looks like a REAL programme title
        (Diploma, Degree, MBA, PhD, etc.) and NOT an entry requirement like
        'A Diploma in any field or its equivalent.'.
        """
        n = (text or "").strip().lower()
        if not n:
            return False

        # Drop obvious template/placeholder labels
        bad = {
            'academic qualification', 'core', 'duration', 'electives', 'english requirement',
            'estimated fees', 'intake dates', 'mpu', 'notes', 'program location',
            'areas of research', 'specialisations', 'instalment payment plan available',
            'terms and conditions apply'
        }
        t = n.strip('* ').strip()
        if t in bad:
            return False

        # 🔎 Entry-requirement style bullets we NEVER want as programme titles
        entry_req_patterns = [
            # existing signals
            r'\bstpm\b',
            r'\bspm\b',
            r'\buec\b',
            r'\bmuet\b',
            r'\bielts\b',
            r'\btoefl\b',
            r'\bcgpa\b',
            r'\bmatriculation\b',
            r'\bfoundation certificate\b',
            r'\bunified examination certificate\b',
            r'\bdiploma\s+or\s+advanced\s+diploma\b',
            r'\byears?\s+of\s+working\s+experience\b',
            r'\bminimum\s+(cgpa|grade|credits?)\b',
            r'\b[0-9]+\s*(?:bs|credits?|passes)\b',

            # NEW: Veritas-style requirement bullets
            r'\bor\s+its\s+equivalent\b',           # "or its equivalent"
            r'\bor\s+equivalent\b',
            r'any\s+field',                         # "in any field"
            r'in\s+(a\s+)?related\s+field',         # "in a related field"
            r'\brelated\s+field\b',
            r'\bminimum\s+[0-9]',                   # "minimum 2 Es"
            r'subject to conditions',               # "subject to conditions laid out ..."
            r'\bmqa\b',                             # "MQA circular ..."
            r'\bfirst\s+class\s+honours?\b',
            r'\b1st\s+class\s+honours?\b',
        ]
        if any(re.search(p, n, flags=re.IGNORECASE) for p in entry_req_patterns):
            return False

        # Also drop bullets that clearly end with "or" (typical in requirement lists)
        # e.g. "A-Levels - minimum 2 Es; or"
        if re.search(r'\bor\W*$', n):
            return False

        # ✅ Accept things that look like real programme titles
        good_patterns = [
            # Foundation / Pre-U
            r'foundation',
            r'pre[-\s]?university',
            r'\ba[-\s]?level(s)?\b',
            r'\bcal\b',

            # Certificate
            r'\bcertificate\b',

            # Diploma
            r'\bdiploma\b',

            # Degree (Bachelor-level)
            r'\bbachelor\b|\bhon(?:ours|s)\b|\bb\.(?:sc|a|eng)\b|\bbsc\b|\bba\b|\bbeng\b',

            # Master / MBA
            r'\bmaster\b|\bm\.?(sc|eng)\b|\bmba\b',

            # PhD / Doctoral
            r'\bph\.?d\b|\bdoctor(?:al|ate)?\b',

            # Pathway / American-style
            r'pathway|american university',

            # 3+0 / 4+0 etc.
            r'\b3\+?0\b|\b4\+?0\b',
        ]
        return any(re.search(p, n, flags=re.IGNORECASE) for p in good_patterns)

    def _parse_markdown_course_groups(self, docs: List[str]) -> Dict[str, List[str]]:
        import re
        groups: Dict[str, List[str]] = {}
        current: Optional[str] = None
        prev_nonempty: Optional[str] = None

        for raw in docs or []:
            if not raw:
                continue

            for line in raw.splitlines():
                stripped = (line or "").strip()

                # --- Headings: "# Foundation & Pre-U", "## Diploma", etc. ---
                h = re.match(r'^\s*#{1,3}\s*(.+?)\s*$', line)
                if h:
                    header = h.group(1).strip()
                    if self._is_level_header(header):
                        current = header
                        groups.setdefault(current, [])
                    else:
                        current = None  # not a level header ⇒ stop grouping here
                    if stripped:
                        prev_nonempty = stripped
                    continue

                # --- Bullets under the current level header ---
                b = re.match(r'^\s*(?:[\-\*\•\u2013]|\d+[.)])\s*(.+?)\s*$', line)
                if b and current:
                    # If the previous non-empty line was an "Entry Requirements" label,
                    # skip these bullets (they are entry requirements, not programmes).
                    if prev_nonempty and re.search(
                        r'(entry requirement|entry requirements|min(?:imum)?\s+requirement)',
                        prev_nonempty,
                        flags=re.IGNORECASE
                    ):
                        if stripped:
                            prev_nonempty = stripped
                        continue

                    item = b.group(1).strip()
                    if self._looks_like_course_title(item):
                        groups[current].append(item)
                    if stripped:
                        prev_nonempty = stripped
                    continue

                # --- Plain text line: remember as context for the next bullet ---
                if stripped:
                    prev_nonempty = stripped

        # De-dupe and drop empty groups
        clean: Dict[str, List[str]] = {}
        for header, items in groups.items():
            uniq = sorted({x for x in items if x}, key=str.lower)
            if uniq:
                clean[header] = uniq
        return clean

    def _groups_from_markdown(self, query_info: Dict) -> Optional[Dict[str, List[str]]]:
        """
        Build programme groups for 'what is the program in X' style queries.

        IMPORTANT:
        - We *do not* re-parse the raw markdown here.
        - We rely only on Chroma metadata (course_title, course_type, university_short).
        This avoids accidentally treating entry-requirement bullets like
        “A recognised Master's Degree approved by the Senate” as programmes.
        """
        # 1) Get all course titles that match the current filters
        titles = self.list_courses(query_info)
        if not titles:
            return None

        # 2) Group them by level (Foundation, Diploma, Degree, Master, etc.)
        return self._categorize_courses_by_level(titles)

    @staticmethod
    def _strip_md_prefixes(s: str) -> str:
        import re
        # Remove leading markdown header/number/bullet prefixes like "## ", "- ", "1) "
        return re.sub(r'^\s*(?:#{1,6}\s*|\*+\s*|\-+\s*|\d+[.)]\s*)', '', (s or '')).strip()

    @classmethod
    def _format_grouped_list(cls, groups: Dict[str, List[str]]) -> str:
        parts: List[str] = []
        for header, items in (groups or {}).items():
            header = cls._strip_md_prefixes(header)
            parts.append(f"**{header}**")  # bold, not "## "
            parts.extend(f"• {cls._strip_md_prefixes(it)}" for it in items if cls._strip_md_prefixes(it))
            parts.append("")
        return "\n".join(parts).strip()

    @classmethod
    def _format_flat_list_from_groups(cls, groups: Dict[str, List[str]]) -> str:
        # Flatten all group items into a single bullet list (deduped, sorted), no headers.
        items: List[str] = []
        for _, lst in (groups or {}).items():
            items.extend(lst or [])
        clean: List[str] = []
        seen = set()
        for it in items:
            t = cls._strip_md_prefixes(it)
            if t and t.lower() != 'certificate':  # avoid stray header-like entries
                k = t.lower()
                if k not in seen:
                    seen.add(k)
                    clean.append(t)
        clean.sort(key=str.lower)
        return "\n".join(f"• {x}" for x in clean)

    # ===================== Prompt & helpers (unchanged) =====================
    def build_prompt(self, query: str, context_chunks: List[Dict], query_info: Dict) -> str:
        import re

        # Collect headings actually present in the context so the LLM can't invent new ones
        allowed_sections = set()
        allowed_semesters = set()
        observed_headings = set()

        context_text = ""
        for i, chunk in enumerate(context_chunks, 1):
            meta = chunk['metadata']
            context_text += f"\n{'=' * 60}\n"

            uni_short = meta.get('university_short', '?')
            uni_name = CANONICAL_UNI_NAMES.get(
                (uni_short or '').strip().upper(),
                meta.get('university_name') or uni_short
            )

            # ✅ Show canonical form inside the [Source] tag to anchor the LLM
            context_text += f"[Source {i}: {_canonical_uni_display(uni_short)} - {meta.get('document_type', '?')}"
            if 'course_id' in meta:
                context_text += f" - {meta.get('course_title', meta.get('course_id'))}"
                if meta.get('section'):
                    context_text += f" - {meta['section']}"
            context_text += "]\n"
            context_text += f"{'=' * 60}\n\n"

            chunk_txt = chunk['content']

            # only simple docs: strip "### URL" blocks
            if (meta.get('document_type') in ('how_to_apply', 'scholarship', 'campus')):
                chunk_txt = _strip_heading_url_blocks(chunk_txt)

            # INTI special-case: Penang-only campus intakes trimming
            if (
                    (meta.get('university_short') == 'INTI')
                    and (meta.get('document_type') == 'courses')
                    and (str(meta.get('section', '')).lower() in {
                'campus intakes', 'intakes', 'fee & intakes', 'campuses & intakes', 'campuses and intakes'
            })
            ):
                chunk_txt_norm = re.sub(
                    r'(INTI\s+International\s+(?:University|College)\s+[A-Za-z ]+)',
                    r'\n\1\n',
                    chunk_txt or '',
                    flags=re.IGNORECASE
                )

                ls = [ln.rstrip() for ln in (chunk_txt_norm or '').splitlines()]

                header_idx = next((i for i, l in enumerate(ls)
                                   if re.search(r'campus(?:es)?\s*&?\s*(?:and\s+)?intakes', l, flags=re.I)), None)

                penang_idx = next((i for i, l in enumerate(ls)
                                   if ('inti' in l.lower() and 'penang' in l.lower())), None)

                if penang_idx is not None:
                    kept = []
                    if header_idx is not None:
                        kept.append(ls[header_idx])
                    kept.append(ls[penang_idx])

                    j = penang_idx + 1
                    while j < len(ls):
                        nxt = ls[j]
                        if re.match(r'^\s*#{1,6}\s*\w', nxt):
                            break
                        if re.match(r'^\s*inti\b', nxt, flags=re.I) and ('penang' not in nxt.lower()):
                            break
                        if re.match(r'^\s*(Programme Structure|Entry Requirements|Fees)\b', nxt, flags=re.I):
                            break
                        if re.match(r'^\s*note[:：]', nxt, flags=re.I):
                            break
                        kept.append(nxt)
                        j += 1

                    chunk_txt = "\n".join([ln for ln in kept if ln.strip()])

            # Fix merged headings like "Year 1## Semester 1"
            chunk_txt = _fix_heading_runs(chunk_txt)

            # collect real headings from content + metadata
            if meta.get('section'):
                allowed_sections.add(str(meta['section']).strip())

            # collect real headings from content + metadata
            if meta.get('section'):
                allowed_sections.add(str(meta['section']).strip())

            # capture markdown headings and common plain headings
            for line in (chunk_txt or "").splitlines():
                line_stripped = line.strip()

                m = re.match(r'^\s*#{1,6}\s*(.+?)\s*$', line_stripped)
                if m:
                    observed_headings.add(m.group(1).strip())

                if re.match(
                        r'^(Electives?|Core(?:\s+Modules)?|Programme Structure|Program Structure|Structure|Course Structure|Curriculum|Modules|Subjects|Syllabus)\s*$',
                        line_stripped, flags=re.IGNORECASE):
                    observed_headings.add(line_stripped)

                ms = re.match(r'^(?:#{1,6}\s*)?(Semester\s+\d+)\s*$', line_stripped, flags=re.IGNORECASE)
                if ms:
                    allowed_semesters.add(ms.group(1).strip().title())

            context_text += (chunk_txt or "")
            context_text += "\n\n"

        allowed_headings = set(h for h in observed_headings if h) | set(
            s for s in allowed_sections if s) | allowed_semesters

        # Detect “structure-like” intent so we can clamp down harder
        structure_like = False
        if query_info.get('doc_type') == 'courses':
            target_section = (query_info.get('section') or '').strip()
            structure_synonyms = {
                'Programme Structure', 'Program Structure', 'Structure', 'Course Structure',
                'Curriculum', 'Modules', 'Subjects', 'Syllabus', 'Core', 'Electives'
            }
            if target_section in structure_synonyms:
                structure_like = True

        if structure_like:
            allowed_headings_list = ", ".join(sorted(allowed_headings)) if allowed_headings else "(none)"
            semesters_note = (
                f"The context contains: {', '.join(sorted(allowed_semesters))}."
                if allowed_semesters else
                "No 'Semester N' headings exist in the context."
            )
            answer_instruction = f"""
        STRICT OUTPUT RULES (follow exactly):
        - Use ONLY headings that appear in the context. Allowed headings are: {allowed_headings_list}
        - Do NOT introduce 'Semester 1/2/3' unless they literally appear. {semesters_note}
        - Do NOT invent categories like 'Academic Modules' unless present verbatim in the context.
        - Reproduce all module names and their descriptions exactly as written; do NOT shorten, summarise, or remove sentences.
        - Preserve the original order from the context. No grouping, renaming, or reordering.
        - Do NOT use Markdown heading markers like '#', '##' or '###' anywhere in your answer.
          If the context shows lines such as 'Year 1' or 'Semester 1' in bold or plain text,
          keep the same formatting and keep 'Year' and 'Semester' on their own lines.
        - If NONE of the context contains the requested section, reply with EXACTLY: I don't have that information.
        - If ANY relevant items exist, output ONLY those items and DO NOT add any disclaimer sentences.

        Output format:
        - Follow the same structure as the context: e.g. 'Year 1', then the module names with their full descriptions underneath.
        - You may add bullet markers before module names, but the text after each module name must be exactly the same as in the context.
        """
        elif query_info['doc_type'] in ['how_to_apply', 'scholarship', 'campus']:
            answer_instruction = """
    Present the answer clearly:
    - Bullet points for lists
    - Numbered steps for procedures
    - Include contact info, addresses, amounts, dates
    - Be complete - don't summarize unless asked
    """
        else:
            answer_instruction = """
    Present the answer in a clear bullet-point format.
    Include all relevant information from the sources.
    Only include a field in the answer if it is mentioned in the context.
    Do not output placeholder lines for missing fields.
    Do NOT include any disclaimer (e.g., "I don't have that information") if you provided any content.
    Only if NONE of the context is relevant, reply with EXACTLY: I don't have that information.
    """

        # ✅ Add a strong, explicit **name policy** the LLM must follow
        name_policy = _uni_name_policy_block()

        prompt = f"""You are a helpful university admission assistant for Malaysian universities.

    CONTEXT INFORMATION:
    {context_text}

    USER QUESTION: {query}

    INSTRUCTIONS:
    1. Answer ONLY based on the context information provided above.
    2. If NONE of the context directly answers the user's request, reply with EXACTLY: I don't have that information. 
       If ANY relevant content exists, do NOT add any disclaimer line.
    3. Always mention which university you're referring to when relevant.
    4. {answer_instruction}
    5. Be accurate - include specific numbers, dates, requirements, contact details.
    6. If comparing multiple universities, organize by university.
    7. {name_policy}
    8. Do NOT add apologies or generic filler.

    YOUR ANSWER:"""
        return prompt

    def _course_title_from_meta(self, meta: Dict) -> Optional[str]:
        raw_id = (meta or {}).get('course_id')
        raw_title = (meta or {}).get('course_title')
        if raw_title:
            t = raw_title.strip()
            if t.startswith('###'):
                return None
            return t
        if raw_id:
            return raw_id.replace('-', ' ').strip().title()
        return None

    def _extract_program_query(self, q: str) -> Dict[str, list]:
        q = q.strip().lower()
        q = re.sub(r'[\?\!\.]+', ' ', q)

        # Try to capture phrase after typical verbs or "degree in ..."
        cand = None
        m = re.search(r'\b(offer|offers|provide|provides|have|has)\s+(?:a|an|the\s+)?(.+)$', q)
        if m: cand = m.group(2)
        if not cand:
            m = re.search(r'\bdegree\s+in\s+(.+)$', q)
            if m: cand = m.group(1)
        if not cand:
            m = re.search(r'\b(?:in|for)\s+(.+)$', q)
            if m: cand = m.group(1)
        if not cand:
            cand = q

        # Trim trailing location qualifiers
        cand = re.sub(r'\b(in|at)\s+(malaysia|penang|pulau pinang|kl|kuala lumpur)\b.*$', '', cand).strip()

        # Drop generic filler words but keep domain words
        drop = {
            'a', 'an', 'the', 'program', 'programme', 'programs', 'programmes', 'course', 'courses',
            'degree', 'diploma', 'certificate', 'foundation', 'pre-university', 'pre', 'university',
            'bachelor', 'masters', 'master', 'doctor', 'phd', 'of', 'in', 'for', 'with', 'and'
        }
        tokens = [t for t in re.findall(r'[a-z0-9\+]+', cand) if t not in drop and len(t) > 1]
        if not tokens:
            tokens = [t for t in re.findall(r'[a-z0-9\+]+', q) if t not in drop and len(t) > 2]

        display = cand.strip().title() if cand else "That Program"
        return {'display': display, 'tokens': tokens, 'raw': cand.strip()}

    def _is_generic_program_query(self, program_query: dict | None) -> bool:
        """True if the user didn't specify a real program (e.g., 'what program')."""
        pq = program_query or {}
        toks = [t.lower() for t in (pq.get('tokens') or [])]
        if not toks:
            return True
        generic = {'what', 'which', 'any', 'all', 'available', 'program', 'programs', 'programme', 'programmes',
                   'course', 'courses', 'degree', 'diploma', 'certificate', 'foundation'}
        # If every token is generic noise, treat as generic (list request)
        return all(t in generic for t in toks)

    def _find_universities_offering_program_generic(self, program_query: Dict) -> Dict[str, list]:
        """
        Return { university_name: [program_title, ...] } using the same semantic gate
        as the level-aware flow, so 'medical' ~ 'medicine', 'business' ~ 'commerce', etc.
        """
        try:
            items = self.collection.get(where={'document_type': 'courses'}, include=['metadatas'])
        except Exception:
            return {}

        out: Dict[str, list] = {}
        for meta in (items.get('metadatas') or []):
            title = self._course_title_from_meta(meta)
            if not title:
                continue
            # Use the robust matcher (token roots + embedding cosine blend)
            if not self._program_gate(title, program_query, min_score=0.55):
                continue

            uni = (meta.get('university_name') or meta.get('university') or meta.get('university_short') or '').strip()
            if not uni:
                continue
            out.setdefault(uni, []).append(title)

        # de-dupe + sort per university
        for uni in list(out.keys()):
            titles = sorted({p for p in out[uni]}, key=str.lower)
            if titles:
                out[uni] = titles
            else:
                out.pop(uni, None)

        # sort universities by name for stable output
        return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))

    def _clean_display_for_level(self, display: str, level: Optional[str]) -> str:
        import re
        s = (display or "").strip()

        # Drop trailing "program/programme" noise
        s = re.sub(r'\b(program(me)?s?)\b', '', s, flags=re.IGNORECASE)

        # If we will show the level separately, strip a leading duplicate level (e.g., "Diploma in ...")
        if level:
            s = re.sub(rf'^\s*{re.escape(level)}\b\s*(in|of)?\s*', '', s, flags=re.IGNORECASE)

        # Collapse spaces and title-case (simple + good-enough)
        s = re.sub(r'\s+', ' ', s).strip()
        return s.title() if s else s

    def _format_offers_list_generic(self, program_query: Dict, mapping: Dict[str, list],
                                    level: Optional[str] = None) -> str:
        disp_raw = program_query.get("display") or "That Program"
        disp = self._clean_display_for_level(disp_raw, level)

        header = (f'**Universities offering: {level.title()} in {disp}**'
                  if level else f'**Universities offering: {disp}**')

        parts = [header]
        for uni, progs in mapping.items():
            parts.append(f'• {uni}')
            for p in progs:
                parts.append(f'  - {p}')
            parts.append('')
        return "\n".join(parts).strip()

    def _resolve_level_from_query_info(self, q: Dict) -> Optional[str]:
        """
        Map user query to a canonical level: foundation | certificate | diploma | degree | master | phd
        Tries course_type_filter first; then maps degree_subtype to a level.
        """
        lvl = (q.get('course_type_filter') or '').lower().strip() or None
        if lvl in ('foundation', 'certificate', 'diploma', 'degree', 'master', 'phd'):
            return lvl
        sub = (q.get('degree_subtype') or '').lower().strip()
        if sub == 'master':
            return 'master'
        if sub in ('doctor', 'phd'):
            return 'phd'
        if sub == 'bachelor':
            return 'degree'
        return None

    def _find_universities_offering_program_with_level(self, program_query: Dict, level: str) -> Dict[str, list]:
        """
        Return { university_display_name: [program_title, ...] } filtered by level (first) and program (second).
        Strict pass filters by metadata course_type via synonyms; relaxed pass infers level from title.
        """
        # 1) Build strict where with course_type synonyms for the level
        syns = COURSE_TYPE_SYNONYMS.get(level.lower(), [level])
        strict_where = {
            '$and': [
                {'document_type': 'courses'},
                {'$or': [{'course_type': s} for s in syns]}
            ]
        }
        try:
            strict = self.collection.get(where=strict_where, include=['metadatas'])
            metas = strict.get('metadatas') or []
        except Exception:
            metas = []

        # If nothing found with strict metadata, relax (fetch all courses once and filter by inferred level)
        if not metas:
            try:
                relaxed = self.collection.get(where={'document_type': 'courses'}, include=['metadatas'])
                metas = relaxed.get('metadatas') or []
            except Exception:
                metas = []

        def uni_name(meta: Dict) -> str:
            return (meta.get('university_name') or meta.get('university') or meta.get('university_short') or '').strip()

        out: Dict[str, list] = {}
        for m in metas:
            # --- LEVEL FILTER FIRST ---
            ct = (m or {}).get('course_type') or ''
            ok_level = False
            if ct:
                ok_level = (ct in syns)
            if not ok_level:
                # fallback to infer from title
                t = (m or {}).get('course_title') or (m or {}).get('course_id') or ''
                inferred = (self._infer_course_type_from_title(t) or '').lower()
                ok_level = (inferred in set(s.lower() for s in syns))
            if not ok_level:
                continue

            # --- PROGRAM FILTER SECOND ---
            title = self._course_title_from_meta(m)
            if not title:
                continue
            if not self._program_gate(title, program_query, min_score=0.55):
                continue

            uni = uni_name(m)
            if not uni:
                continue
            out.setdefault(uni, []).append(title)

        # de-dupe and sort titles per uni
        for u in list(out.keys()):
            dedup_sorted = sorted({p for p in out[u]}, key=str.lower)
            if dedup_sorted:
                out[u] = dedup_sorted
            else:
                out.pop(u, None)

        # sort universities by name
        return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))

    # ======== Fee extraction & comparison helpers ========
    def _title_matches_program(self, title: str, program_query: Dict) -> bool:
        """
        Fast path check: every query root must have a close match in the title roots.
        Rooting collapses variants like studies/study, engineering/engineer, etc.
        """
        raw = (program_query or {}).get('raw') or ' '.join((program_query or {}).get('tokens') or [])
        q_roots = _token_roots_from_text(raw)
        t_roots = _token_roots_from_text(title)
        if not q_roots or not t_roots:
            return False
        return all(any(q == tr or tr.startswith(q) or q.startswith(tr) for tr in t_roots) for q in q_roots)

    def _program_score(self, title: str, program_query: Dict) -> float:
        """
        Score how well a course title matches the user's program idea.
        - Token/root overlap (precise, cheap)
        - Embedding cosine (robust to wording like 'Commerce' vs 'Business')
        Blend favors tokens but lets embeddings rescue near-synonyms.
        """
        raw_phrase = (program_query or {}).get('raw') or ' '.join((program_query or {}).get('tokens') or [])
        q_roots = _token_roots_from_text(raw_phrase)
        t_roots = _token_roots_from_text(title)

        # token/root overlap (with light fuzzy via prefix equality)
        token_score = 0.0
        if q_roots and t_roots:
            hits = 0
            for q in q_roots:
                if any(q == tr or tr.startswith(q) or q.startswith(tr) for tr in t_roots):
                    hits += 1
                else:
                    # tiny fuzzy backstop (difflib) so 'finance' ~ 'financial'
                    best = max((difflib.SequenceMatcher(None, q, tr).ratio() for tr in t_roots), default=0.0)
                    if best >= 0.82:
                        hits += 1
            token_score = hits / max(1, len(q_roots))

        # embedding cosine
        emb_score = 0.0
        try:
            em = LISettings.embed_model  # already set in __init__
            qv = em.get_text_embedding(raw_phrase or title)
            tv = em.get_text_embedding(title)
            emb_score = _cosine(qv, tv)
        except Exception:
            pass

        return 0.65 * token_score + 0.35 * emb_score

    def _program_gate(self, title: str, program_query: Dict, min_score: float = 0.62) -> bool:
        """
        True if the course title is a plausible match for the program family.
        - Fast path: all query tokens appear in title tokens.
        - Otherwise: blended score must clear a slightly higher bar AND share at least one rooted token.
        """
        if self._title_matches_program(title, program_query):
            return True

        score = self._program_score(title, program_query)

        if score < min_score:
            return False

        # Require at least one common root to avoid "Computer Science" ≈ "Information Technology" false positives
        q_roots = _token_roots_from_text(
            (program_query or {}).get('raw') or ' '.join((program_query or {}).get('tokens') or []))
        t_roots = _token_roots_from_text(title)
        return any(q in t_roots for q in q_roots)

    def _looks_like_fee_chunk(self, text: str, meta: Dict) -> bool:
        sec = (meta or {}).get('section') or ''
        if sec in FEE_SECTION_SYNONYMS:
            return True

        t = (text or '').lower()

        # Strong hints it’s a fees block even if the section label is unusual
        feeish_keywords = [
            'fee', 'fees', 'tuition', 'per semester', 'per year', 'per annum',
            'programme fee', 'program fee'
        ]
        if 'rm' in t and any(k in t for k in feeish_keywords):
            return True

        # Backstop: many RM amounts on one chunk ⇒ likely a fee table
        rm_hits = re.findall(r'\brm\s*[0-9]', t)
        return len(rm_hits) >= 2

    def _maybe_build_veritas_fee_image_answer(
        self,
        query_info: Dict,
        context_chunks: List[Dict],
    ) -> Optional[str]:
        # Only care about VERITAS course fee questions
        if (
            query_info.get("doc_type") != "courses"
            or (query_info.get("university") or "").upper() != "VERITAS"
        ):
            return None

        sec = query_info.get("section") or ""
        if sec not in ("Fees", "Fee & Intakes"):
            return None

        image_urls: list[str] = []

        # Look only at fee-like chunks for this answer
        for ch in context_chunks or []:
            meta = ch.get("metadata") or {}
            if (meta.get("document_type") or "").lower() != "courses":
                continue

            sec_label = meta.get("section") or ""
            if sec_label not in FEE_SECTION_SYNONYMS:
                continue

            txt = ch.get("content") or ch.get("text") or ch.get("document") or ""

            # If we can parse ANY RM amount, treat as normal fee text → bail out
            if _find_all_rm_amounts_with_pos(txt):
                return None

            # Collect any markdown image URLs
            for u in _extract_md_image_urls(txt):
                if u not in image_urls:
                    image_urls.append(u)

        if not image_urls:
            return None

        # Build a simple body that just shows the image(s).
        # (LLM will *only* echo this text via _echo_via_llm_segmented.)
        lines = [
            "The fee structure is provided as an image by Veritas College. "
            "Please refer to the fee structure below:"
        ]
        for u in image_urls:
            lines.append("")
            lines.append(f"![Fee Structure]({u})")

        return "\n".join(lines).strip()

    def _compare_fees_for_program(
            self,
            program_query: Dict,
            course_type: Optional[str],
            uni_whitelist: Optional[list]
    ) -> Tuple[str, List[Dict]]:

        def _fetch(where_clause):
            try:
                return self.collection.get(where=where_clause, include=['documents', 'metadatas'])
            except Exception:
                return {'documents': [], 'metadatas': []}

        # Normalise course_type to a level and collect synonyms if available
        type_syns = None
        if course_type:
            key = course_type.lower()
            type_syns = COURSE_TYPE_SYNONYMS.get(key, [course_type])

        # ----- PASS 1: strict (prefer known fee sections; keep course_type if provided)
        strict_where = {
            '$and': [
                {'document_type': 'courses'},
                {'$or': [{'section': s} for s in FEE_SECTION_SYNONYMS]}
            ]
        }

        # Use course_type synonyms where possible
        if type_syns:
            strict_where['$and'].append(
                {'$or': [{'course_type': v} for v in type_syns]}
            )

        if uni_whitelist:
            strict_where['$and'].append({'$or': [{'university_short': u} for u in uni_whitelist]})

        items = _fetch(strict_where)
        docs = items.get('documents') or []
        metas = items.get('metadatas') or []

        # If strict returns nothing, try a relaxed pass (drop section & course_type filters)
        if not metas:
            relaxed_where = {'$and': [{'document_type': 'courses'}]}
            if uni_whitelist:
                relaxed_where['$and'].append({'$or': [{'university_short': u} for u in uni_whitelist]})
            items = _fetch(relaxed_where)
            docs = items.get('documents') or []
            metas = items.get('metadatas') or []

        # Keep best-matching fee chunk per university (LOCAL/Malaysian fee only)
        per_uni: Dict[str, Dict] = {}
        for d, m in zip(docs, metas):
            m = m or {}
            uni = m.get('university_short')
            title = self._course_title_from_meta(m) or ''
            if not uni or not title or not d:
                continue

            # Require the chunk to look like a fees block (relaxed above)
            if not self._looks_like_fee_chunk(d, m):
                continue

            # Ensure the programme title matches the user's idea (e.g., "Foundation in Arts")
            if not self._program_gate(title, program_query, min_score=0.55):
                continue

            # ✅ Extract fees BEFORE using them
            fees = _extract_fees_from_text(d)
            local_fee = fees.get('local')

            if local_fee is None:
                # no usable fee at all → skip
                continue

            block_low = (d or "").lower()

            tiny_amount = local_fee < 300
            looks_like_admin_fee = any(
                kw in block_low
                for kw in [
                    "application fee",
                    "registration fee",
                    "processing fee",
                    "admin fee",
                    "administrative fee",
                    "caution deposit",
                    "security deposit",
                    "deposit"
                ]
            )

            if tiny_amount and looks_like_admin_fee:
                continue

            score = self._program_score(title, program_query)
            cur = per_uni.get(uni)
            if (cur is None) or (score > cur['score']):
                url = next((m.get(k) for k in ('url', 'source_url', 'page_url', 'pdf_url') if m.get(k)), None)
                if not url:
                    extra = _extract_any_urls(d)
                    url = extra[0] if extra else None
                per_uni[uni] = {
                    'uni': uni,
                    'title': title,
                    'local': local_fee,
                    'course_id': m.get('course_id'),
                    'url': url,
                    'score': score
                }

        # ----- PASS 2 (SALVAGE): if we still have few matches, search any chunk of matching courses for RM -----
        if len(per_uni) < 3:
            # Salvage pass: drop course_type from the DB filter and enforce level in Python.
            salvage_where = {'$and': [{'document_type': 'courses'}]}
            if uni_whitelist:
                salvage_where['$and'].append(
                    {'$or': [{'university_short': u} for u in uni_whitelist]}
                )

            it2 = _fetch(salvage_where)
            docs2 = it2.get('documents') or []
            metas2 = it2.get('metadatas') or []

            for d, m in zip(docs2, metas2):
                m = m or {}
                uni = m.get('university_short')
                if not uni or uni in per_uni:
                    continue  # already have a fee for this uni

                title = self._course_title_from_meta(m) or ''
                if not title:
                    continue

                # Still require the programme to match the requested "family"
                if not self._program_gate(title, program_query, min_score=0.52):
                    continue

                # If a level (course_type) was requested, enforce it using metadata + title.
                if course_type:
                    meta_ct = m.get('course_type')
                    if meta_ct and not self._course_type_matches(meta_ct, course_type):
                        # Metadata clearly says it's a different level
                        continue

                    inferred_level = self._infer_course_type_from_title(title)
                    if inferred_level and inferred_level.lower() != course_type.lower():
                        continue

                fees = _extract_fees_from_text(d)
                local_fee = fees.get('local')
                block_low = (d or "").lower()

                if local_fee is None:
                    continue

                tiny_amount = local_fee < 300
                looks_like_admin_fee = any(
                    kw in block_low
                    for kw in [
                        "application fee",
                        "registration fee",
                        "processing fee",
                        "admin fee",
                        "administrative fee",
                        "caution deposit",
                        "security deposit",
                        "deposit",
                    ]
                )

                if tiny_amount and looks_like_admin_fee:
                    continue

                score = self._program_score(title, program_query)
                url = next((m.get(k) for k in ('url', 'source_url', 'page_url', 'pdf_url') if m.get(k)), None)
                if not url:
                    extra = _extract_any_urls(d)
                    url = extra[0] if extra else None

                per_uni[uni] = {
                    'uni': uni,
                    'title': title,
                    'local': local_fee,
                    'course_id': m.get('course_id'),
                    'url': url,
                    'score': score
                }

        if not per_uni:
            return ("I couldn't find fee data for that program.", [])

        # Build Top 3 (LOCAL only)
        top3 = sorted(per_uni.values(), key=lambda x: x['local'])[:3]

        lines = []
        lines.append("**Top 3 Cheapest**")
        for v in top3:
            try:
                shown = int(v['local'])
            except Exception:
                shown = v['local']
            lines.append(f"• {v['uni']} — RM{shown:,}  _(course: {v['title']})_")
        lines.append("")
        text = "\n".join(lines).strip()

        # Sources for the UI
        sources = []
        for v in per_uni.values():
            sources.append({
                "university": v['uni'],
                "document": "courses",
                "course": v.get("course_id"),
                "section": "Fees",
                "url": v.get("url")
            })

        return text, sources

    def _extract_elective_notes(self, chunks: List[Dict]) -> List[str]:
        notes = []
        for ch in chunks:
            txt = ch.get("content", "")
            for line in txt.splitlines():
                line_clean = line.strip(" •*-").strip()
                if re.match(r'^(Elective[s]?\s*\([^)]+\))$', line_clean, flags=re.IGNORECASE):
                    notes.append(line_clean)
        seen = set()
        out = []
        for n in notes:
            k = n.lower()
            if k not in seen:
                seen.add(k)
                out.append(n)
        return out

    def _inject_elective_note_under_semester(self, text: str, target_semester: str, notes: List[str]) -> str:
        if not notes:
            return text
        if any(n.lower() in text.lower() for n in notes):
            return text
        pattern = r'(^[•\-\*]\s*' + re.escape(target_semester) + r'\s*$)'
        lines = text.splitlines()
        out = []
        i = 0
        inserted = False
        while i < len(lines):
            out.append(lines[i])
            if not inserted and re.match(pattern, lines[i].strip(), flags=re.IGNORECASE):
                for n in notes:
                    out.append("  - " + n)
                inserted = True
            i += 1
        return "\n".join(out)

    def _section_synonyms(self, name: str) -> list:
        mp = {
            'Programme Structure': ['Programme Structure', 'Program Structure', 'Structure', 'Course Structure',
                                    'Curriculum', 'Modules', 'Subjects', 'Syllabus'],
            'Core': ['Core', 'Programme Structure', 'Program Structure', 'Structure', 'Course Structure', 'Curriculum',
                     'Modules', 'Subjects', 'Syllabus'],
            'Electives': ['Electives', 'Elective', 'Optional', 'Optional Modules'],
        }
        return mp.get(name, [name])

    def _fetch_course_structure_chunks(self, course_id: str, uni_short: Optional[str] = None) -> List[Dict]:
        """Fetch ALL chunks for a course that belong to structure/core and electives."""
        if not course_id:
            return []

        # all section labels we want to include in the answer
        wanted_sections = set(self._section_synonyms('Programme Structure') +
                              self._section_synonyms('Core') +
                              self._section_synonyms('Electives'))

        where_clause = {
            '$and': [
                {'document_type': 'courses'},
                {'course_id': course_id},
                {'$or': [{'section': s} for s in sorted(wanted_sections)]}
            ]
        }
        if uni_short:
            # Guard against cross-uni collisions on shared course_id slugs
            where_clause['$and'].append({'university_short': uni_short})

        try:
            items = self.collection.get(where=where_clause, include=['documents', 'metadatas'])
        except Exception:
            return []

        docs = items.get('documents', [])
        metas = items.get('metadatas', [])
        out: List[Dict] = []
        for d, m in zip(docs, metas):
            if not d:
                continue
            out.append({
                'content': d,
                'metadata': m or {},
                'relevance_score': 1.0,  # force top priority
            })

        # keep a natural reading order if hints exist
        def _order_key(c):
            m = c.get('metadata') or {}
            return (
                m.get('page', 0),
                m.get('chunk_index', m.get('chunk', m.get('i', 0)))
            )

        out.sort(key=_order_key)
        return out

    def _build_sources_for_course(
            self,
            course_id: str,
            uni_short: Optional[str],
            section: Optional[str] = None,
            limit: int = 6,
    ) -> List[Dict]:
        """
        Build source URLs for a single course_id (e.g. when answering Programme Structure).

        STRICT rule for courses:
        - First, use the '### URL' block from Courses.md for that course.
        - If that is missing, fall back to metadata URL fields.
        - Only as a last resort, fall back to generic scoring.
        This prevents cross-links (e.g. other programmes on the same page) from stealing the source.
        """
        if not course_id:
            return []

        where = {
            "$and": [
                {"document_type": "courses"},
                {"course_id": course_id},
            ]
        }
        if uni_short:
            where["$and"].append({"university_short": uni_short})

        try:
            items = self.collection.get(
                where=where,
                include=["documents", "metadatas"],
            )
        except Exception:
            return []

        docs = items.get("documents") or []
        metas = items.get("metadatas") or []

        if not docs:
            return []

        section_name = section or "Programme Structure"
        base = {
            "university": uni_short,
            "document": "courses",
            "course": course_id,
            "section": section_name,
        }

        # we still cap at 2 like before, but respect 'limit' if caller wants fewer
        max_urls = max(1, min(limit, 2))

        # 1) STRONGEST: use '### URL' heading from the Courses.md block
        heading_urls: list[str] = []
        seen: set[str] = set()
        for d in docs:
            if not d:
                continue
            for u in _extract_heading_urls(d):
                u = (u or "").strip()
                if u and u not in seen:
                    seen.add(u)
                    heading_urls.append(u)

        if heading_urls:
            return [
                {**base, "url": u, "score": 999.0}
                for u in heading_urls[:max_urls]
            ]

        # 2) FALLBACK: use metadata URL fields (page_url/url/source_url/pdf_url/link)
        accepted: list[str] = []
        fallback_one: Optional[str] = None

        for m in metas or []:
            m = m or {}
            title = m.get("course_title")
            for key in ("page_url", "url", "source_url", "pdf_url", "link"):
                u = (m.get(key) or "").strip()
                if not u:
                    continue
                if not fallback_one:
                    fallback_one = u  # remember first URL as very last resort
                # Prefer URLs that actually look like this course
                if _url_seems_for_course(u, course_id, title) and u not in accepted:
                    accepted.append(u)

        urls = accepted or ([fallback_one] if fallback_one else [])
        if urls:
            return [
                {**base, "url": u, "score": 999.0}
                for u in urls[:max_urls]
            ]

        # 3) LAST RESORT: fall back to generic scoring over all URLs in these docs
        chunks: List[Dict] = []
        for d, m in zip(docs, metas):
            if not d:
                continue
            chunks.append({
                "content": d,
                "metadata": m or {},
                "relevance_score": 1.0,
            })

        if not chunks:
            return []

        qi = {
            "doc_type": "courses",
            "course_id_filter": course_id,
            "section": section_name,
        }
        # here we pass 'limit' down to the generic helper
        return _best_course_urls_from_chunks(chunks, qi, limit=limit)

    def _build_sources_for_answer(
            self,
            query_info: Dict,
            context_chunks: List[Dict],
            limit: int = 6,
    ) -> List[Dict]:
        if isinstance(query_info, dict):
            qi = dict(query_info)  # shallow copy
        else:
            qi = query_info or {}
        doc_type = (qi.get("doc_type") or "").lower()

        # ----- Single-course answers (subjects/fees/duration/entry/intakes) -----
        if doc_type == "courses" and not qi.get("compare_fees"):
            course_id: Optional[str] = qi.get("course_id_filter") or None

            # 1) If generate_response already resolved a course, reuse it
            if not course_id and getattr(self, "_last_course_id", None):
                course_id = self._last_course_id

            # 2) If still empty, read course_id from the chunks that were actually used
            if not course_id:
                for ch in context_chunks or []:
                    md = ch.get("metadata") or {}
                    cid = md.get("course_id")
                    if cid:
                        course_id = cid
                        # also fill university if missing
                        if not qi.get("university") and md.get("university_short"):
                            qi["university"] = md["university_short"]
                        break

            # Work out university + section
            uni_short = qi.get("university")
            if not uni_short and getattr(self, "_last_course_university", None):
                uni_short = self._last_course_university

            section = qi.get("section")

            # If we know which course this answer came from, always use the
            # canonical course URL(s) for that course only.
            if course_id:
                course_sources = self._build_sources_for_course(
                    course_id,
                    uni_short,
                    section=section,
                    limit=limit,
                )
                if course_sources:
                    return course_sources

        # ----- Fallback: generic URL selection from the same chunks used to answer -----
        # (used for non-course answers, lists, etc.)
        return _best_course_urls_from_chunks(context_chunks or [], qi, limit=limit)

    def _fetch_all_scholarship_chunks(self, uni_short: str) -> List[Dict]:
        """Return ALL scholarship chunks for a university in natural reading order."""
        if not uni_short:
            return []

        where_clause = {
            '$and': [
                {'document_type': 'scholarship'},
                {'university_short': uni_short}
            ]
        }
        try:
            items = self.collection.get(where=where_clause, include=['documents', 'metadatas'])
        except Exception:
            return []

        docs = items.get('documents', []) or []
        metas = items.get('metadatas', []) or []

        chunks: List[Dict] = []
        for d, m in zip(docs, metas):
            if not d:
                continue
            chunks.append({
                'content': d,
                'metadata': m or {},
                'relevance_score': 1.0  # not actually ranking
            })

        # Keep a consistent reading order when page/chunk hints exist
        def _order_key(c: Dict) -> tuple:
            md = c.get('metadata') or {}
            return (
                md.get('page', 0),
                md.get('chunk_index', md.get('chunk', md.get('i', 0)))
            )

        chunks.sort(key=_order_key)
        return chunks

    def _stitch_markdown(self, chunks: List[Dict]) -> str:
        """Join ordered scholarship chunks into one markdown string (dedupe exact repeats)."""
        seen = set()
        parts: List[str] = []
        for ch in chunks or []:
            t = (ch.get('content') or '').strip()
            if not t or t in seen:
                continue
            seen.add(t)
            parts.append(t)
        return "\n\n".join(parts).strip()

    def _fetch_all_campus_chunks(self, uni_short: str) -> List[Dict]:
        """Return ALL campus/location chunks for a university in natural reading order."""
        if not uni_short:
            return []

        where_clause = {
            '$and': [
                {'document_type': 'campus'},
                {'university_short': uni_short}
            ]
        }
        try:
            items = self.collection.get(where=where_clause, include=['documents', 'metadatas'])
        except Exception:
            return []

        docs = items.get('documents', []) or []
        metas = items.get('metadatas', []) or []

        chunks: List[Dict] = []
        for d, m in zip(docs, metas):
            if not d:
                continue
            chunks.append({
                'content': d,
                'metadata': m or {},
                'relevance_score': 1.0  # not ranking; we’ll order below
            })

        def _order_key(c: Dict) -> tuple:
            md = c.get('metadata') or {}
            return (
                md.get('page', 0),
                md.get('chunk_index', md.get('chunk', md.get('i', 0)))
            )

        chunks.sort(key=_order_key)
        return chunks

    def _fetch_all_howto_chunks(self, uni_short: str) -> List[Dict]:
        """Return ALL how_to_apply chunks for a university in natural reading order."""
        if not uni_short:
            return []

        where_clause = {
            '$and': [
                {'document_type': 'how_to_apply'},
                {'university_short': uni_short}
            ]
        }
        try:
            items = self.collection.get(where=where_clause, include=['documents', 'metadatas'])
        except Exception:
            return []

        docs = items.get('documents') or []
        metas = items.get('metadatas') or []

        chunks: List[Dict] = []
        for d, m in zip(docs, metas):
            if not d:
                continue
            chunks.append({
                'content': d,
                'metadata': m or {},
                'relevance_score': 1.0
            })

        def _order_key(c: Dict) -> tuple:
            md = c.get('metadata') or {}
            return (
                md.get('page', 0),
                md.get('chunk_index', md.get('chunk', md.get('i', 0)))
            )

        chunks.sort(key=_order_key)
        return chunks

    # ===================== Generation (unchanged logic, now with options everywhere) =====================
    async def generate_response(self,query: str,university_filter: Optional[str] = None,stream: bool = True) -> AsyncGenerator[str, None]:
        request_id = uuid.uuid4().hex
        t0 = time.time()
        log_event("query_received", request_id=request_id, query=query, university_filter=university_filter)

        retrieve_ms = 0
        llm_ms = 0
        retrieved_count = 0
        top_rel = None

        self._last_request_id = request_id
        self._last_query = query
        self._last_sources = []
        self._last_university_filter = university_filter

        def _summ(status: str, error: Optional[str] = None):
            log_summary(
                event="query_summary",
                request_id=request_id,
                query=query,
                university=(query_info.get('university') if 'query_info' in locals() else None),
                doc_type=(query_info.get('doc_type') if 'query_info' in locals() else None),
                section=(query_info.get('section') if 'query_info' in locals() else None),
                retrieved=retrieved_count,
                top_relevance=round(top_rel, 3) if isinstance(top_rel, (int, float)) else None,
                retrieve_ms=int(retrieve_ms),
                llm_ms=int(llm_ms),
                total_ms=int((time.time() - t0) * 1000),
                model=self.model_name,
                reranker=bool(self.use_reranker),
                status=status,
                error=error
            )

        query_info = self.detect_query_type(query)
        if university_filter:
            query_info['university'] = university_filter
        log_event("query_detected", request_id=request_id, detected=query_info)

        # === deterministic fee comparison ===
        if query_info.get('compare_fees'):
            uni_whitelist = _detect_universities_in_text(query)
            # also include the single filter if user used the dropdown
            if query_info.get('university') and query_info['university'] not in uni_whitelist:
                uni_whitelist.append(query_info['university'])

            pq = query_info.get('program_query') or {}
            course_type = query_info.get('course_type_filter')
            text, sources = self._compare_fees_for_program(pq, course_type, uni_whitelist or None)

            # sources for /api/chat/sources
            self._last_sources = sources
            self._last_request_id = request_id
            self._last_query = query

            hdr = self._build_title(query_info)
            for out in self._emit_via_llm(text, stream, title=hdr):
                yield out
            _summ("ok_fee_compare")
            return

        # === “list universities in Penang” intent ===
        if query_info.get('list_universities'):
            region = 'penang'
            names = self.list_universities(region=region)

            if names:
                list_text = "\n".join(f"• {n}" for n in names)
                hdr = self._build_title(query_info, region=region)
                for out in self._emit_via_llm(list_text, stream, title=hdr):
                    yield out
                _summ("ok_uni_list")
                return
            else:
                text = "I don't have a Penang university list yet."
                hdr = self._build_title(query_info, region=region)
                for out in self._emit_via_llm(text, stream, title=hdr):
                    yield out
                _summ("ok_uni_list_empty")
                return

        # === COURSES HANDLER (list-first) ===
        if query_info.get('course_query'):
            # 0) PURE LIST MODE — run BEFORE best-match/structure flow
            if query_info.get('list_query') and not query_info.get('section') and (
                    not query_info.get('offer_lookup') or self._is_generic_program_query(
                query_info.get('program_query'))
            ):
                # Try to read grouped items from your categorized markdown
                groups_md = self._groups_from_markdown(query_info)
                if groups_md:
                    text = self._format_grouped_list(groups_md)
                    hdr = self._build_title(query_info)
                    for out in self._emit_via_llm(text, stream, title=hdr):
                        yield out
                    _summ("ok_list_from_md_grouped")
                    return

                # Fallback: list by titles then output flat bullets
                titles = self.list_courses(query_info)
                if titles:
                    # Group by level and render with headers (bold)
                    groups = self._categorize_courses_by_level(titles)
                    text = self._format_grouped_list(groups)
                    hdr = self._build_title(query_info)
                    for out in self._emit_via_llm(text, stream, title=hdr):
                        yield out
                    _summ("ok_list_grouped")
                    return

                uni = query_info.get('university')
                text = f"No programs found for {uni}." if uni else "No programs found."
                hdr = self._build_title(query_info)
                for out in self._emit_via_llm(text, stream, title=hdr):
                    yield out
                _summ("ok_list_empty")
                return

            # 1) OFFER LOOKUP (Which universities offer <program> [at level] [in Penang] …)
            if query_info.get('offer_lookup'):
                pq = query_info.get('program_query') or {}

                # Resolve level from query (e.g., diploma/degree/master/phd)
                level = self._resolve_level_from_query_info(query_info)

                if level:
                    mapping = self._find_universities_offering_program_with_level(pq, level)
                else:
                    mapping = self._find_universities_offering_program_generic(pq)

                # Respect explicit university filter (e.g., “at UOW”)
                uni_short = query_info.get('university')
                if uni_short and mapping:
                    want = uni_short.upper()
                    alias_tokens = {want.lower()}
                    for k, v in UNIVERSITY_ALIASES.items():
                        if v == want:
                            alias_tokens.add(k.lower())
                    mapping = {
                        name: progs
                        for name, progs in mapping.items()
                        if any(tok in (name or "").lower() for tok in alias_tokens)
                    }

                if mapping:
                    text = self._format_offers_list_generic(pq, mapping, level=level)
                    hdr = self._build_title(query_info)
                    for out in self._emit_via_llm(text, stream, title=hdr):
                        yield out
                    _summ("offer_lookup_ok")
                    return
                else:
                    who = f" for {uni_short}" if uni_short else ""
                    extra = f" at the {level.title()} level" if level else ""
                    text = f"I couldn't find any universities offering that program{extra}{who}."
                    hdr = self._build_title(query_info)
                    for out in self._emit_via_llm(text, stream, title=hdr):
                        yield out
                    _summ("offer_lookup_empty")
                    return

            # 2) BEST-MATCH FLOW (structure/fees/entry/intakes)
            best = self._match_course(query, query_info.get('university'))
            if best:
                query_info['course_id_filter'] = best['course_id']
                # Remember this as the "current course" for follow-up questions
                self._last_course_id = best['course_id']
                self._last_course_university = query_info.get('university')
                self._last_course_title = best.get('course_title')

            # If the user didn't mention a course/university explicitly, try to
            # reuse the last successfully-resolved course from this conversation.
            if not query_info.get('university') and not query_info.get('course_id_filter'):
                if self._last_course_id:
                    query_info['course_id_filter'] = self._last_course_id
                    if self._last_course_university:
                        query_info['university'] = self._last_course_university

                # After trying conversation context, if we STILL have no course
                # or university, fall back to a clear clarification message.
                if not query_info.get('university') and not query_info.get('course_id_filter'):
                    msg = (
                        "Duration, intakes, subjects and entry requirements depend on "
                        "the specific programme and university. Please tell me which "
                        "course and university."
                    )
                    log_event(
                        "query_needs_specificity",
                        request_id=request_id,
                        reason="missing_university_and_course_no_context"
                    )
                    for out in self._emit_via_llm(msg, stream):
                        yield out
                    _summ("needs_specificity")
                    return

        # === END COURSES HANDLER ===

        is_structure_q = (
                query_info.get('doc_type') == 'courses' and
                query_info.get('section') in (
                    'Programme Structure', 'Program Structure', 'Structure',
                    'Course Structure', 'Curriculum', 'Modules', 'Subjects'
                )
        )

        # --- SIMPLE DOCS PASSTHROUGH: echo full Markdown (minus ### URL) for how_to_apply / scholarship / campus ---
        simple_doc = query_info.get('doc_type') if query_info.get('doc_type') in ('scholarship', 'campus',
                                                                                  'how_to_apply') else None
        has_uni = bool(query_info.get('university'))

        t_retr0 = time.time()
        if simple_doc and has_uni:
            if simple_doc == 'scholarship':
                context_chunks = self._fetch_all_scholarship_chunks(query_info['university'])
            elif simple_doc == 'campus':
                context_chunks = self._fetch_all_campus_chunks(query_info['university'])
            else:  # how_to_apply
                context_chunks = self._fetch_all_howto_chunks(query_info['university'])

            retrieve_ms = (time.time() - t_retr0) * 1000
            retrieved_count = len(context_chunks)
            top_rel = 1.0 if context_chunks else None

            self._last_sources = []
            seen = set()
            uni_short = query_info['university']
            for ch in context_chunks:
                text = ch.get('content') or ''
                for u in _extract_heading_urls(text):
                    if u in seen:
                        continue
                    seen.add(u)
                    self._last_sources.append({
                        "university": uni_short,
                        "document": simple_doc,
                        "course": None,
                        "section": None,
                        "url": u
                    })

            # Stitch everything and remove just the "### URL" section
            stitched = self._stitch_markdown(context_chunks)
            stitched = _strip_heading_url_blocks(stitched)
            if not stitched:
                for out in self._emit_via_llm(
                        f"No {simple_doc.replace('_', ' ')} information found for {uni_short}.",
                                              stream):
                    yield out
                _summ(f"{simple_doc}_empty")
                return

            title = self._build_title(query_info, context_chunks)
            for out in self._echo_via_llm_segmented(stitched, title=title, stream=True):
                yield out
            _summ(f"ok_{simple_doc}_echo")
            return

        # Fallback to normal retrieval for everything else
        n_results = 12 if is_structure_q else (
            1 if query_info['doc_type'] in ['how_to_apply', 'campus', 'scholarship'] else 6)
        context_chunks = self.retrieve_context(query, query_info, n_results=n_results, request_id=request_id)
        retrieve_ms = (time.time() - t_retr0) * 1000
        retrieved_count = len(context_chunks)
        top_rel = (context_chunks[0]['relevance_score'] if context_chunks else None)


        # --- structure questions: canonical structure echo (Subjects == Programme Structure) ---
        if is_structure_q:
            import re
            # Target course: from best-match OR from retrieved chunks
            target_course = (
                    query_info.get('course_id_filter') or
                    next(((ch.get('metadata') or {}).get('course_id')
                          for ch in context_chunks
                          if (ch.get('metadata') or {}).get('course_id')), None)
            )

            # 1) Prefer the full set (Programme Structure + Core + Electives) from the index
            stitched = ""
            if target_course:
                # Prefer to constrain by university to avoid cross-uni collisions on shared course_ids
                uni_short_for_course = (
                        query_info.get('university')
                        or next(((ch.get('metadata') or {}).get('university_short') for ch in context_chunks
                                 if (ch.get('metadata') or {}).get('course_id') == target_course), None)
                )
                full_chunks = self._fetch_course_structure_chunks(target_course, uni_short=uni_short_for_course)

                # 🔒 Lock /api/chat/sources to this exact course for structure questions
                try:
                    self._last_sources = self._build_sources_for_course(
                        course_id=target_course,
                        uni_short=uni_short_for_course,
                        section=query_info.get("section"),
                    )
                except Exception:
                    # If anything goes wrong, fall back to empty; other flows still work
                    self._last_sources = []

                if full_chunks:
                    def _clean_structure_text(s: str) -> str:
                        # drop fee/discount/scholarship noise that sometimes appears on course pages
                        bad = ('rm', 'fee', 'fees', 'tuition', 'discount', 'waiver', 'scholarship')
                        txt = "\n".join(
                            ln for ln in (s or "").splitlines()
                            if not any(b in ln.lower() for b in bad)
                        ).strip()
                        # strip a leading "Programme Structure" heading (with/without ###) to prevent duplicates when stitching
                        txt = re.sub(r'(?mi)^\s*(?:#{1,6}\s*)?programme\s+structure\s*\n+', '', txt)
                        return txt

                    stitched = "\n\n".join(
                        _clean_structure_text(t)
                        for t in ((c.get('content') or '').strip() for c in full_chunks)
                        if t and _looks_like_structure_text(t)
                    ).strip()

            # 2) Salvage path: if nothing found above, use structure-like chunks from retrieval
            if not stitched and context_chunks:
                salvage = []
                for ch in context_chunks:
                    t = (ch.get('content') or '').strip()
                    if not t:
                        continue
                    m = ch.get('metadata') or {}
                    sec = (m.get('section') or '')

                    # Keep clear structure sections or obvious semester/year blocks
                    if sec in (
                            self._section_synonyms('Programme Structure')
                            + self._section_synonyms('Core')
                            + self._section_synonyms('Electives')
                    ):
                        salvage.append(t)
                    elif re.search(r'\b(Semester\s+\d+|Year\s+\d+)\b', t, flags=re.I):
                        salvage.append(t)

                if salvage:
                    stitched = "\n\n".join(salvage).strip()

            # 3) If we have structure text, strip any inner "Programme Structure" heading
            #    and let the big H1 title be the only section title.
            if stitched:
                # Remove lines that are just "Programme Structure" headings
                stitched = re.sub(
                    r'(?mi)^\s*(?:#{1,6}\s*)?programme\s+structure\s*$\n?',
                    '',
                    stitched,
                )
                # Also handle heading followed by a blank line
                stitched = re.sub(
                    r'(?mi)^\s*(?:#{1,6}\s*)?programme\s+structure\s*\n\s*\n',
                    '\n',
                    stitched,
                )
                stitched = stitched.lstrip()

                # Build sources for structure answers (previously missing)
                self._last_sources = []
                seen = set()
                used_chunks = full_chunks if ('full_chunks' in locals() and full_chunks) else context_chunks
                for ch in (used_chunks or []):
                    meta = ch.get('metadata') or {}
                    txt = ch.get('content') or ch.get('text') or ch.get('document') or ''
                    # 1) meta-based URL
                    url = next((meta.get(k) for k in ('url', 'source_url', 'page_url', 'pdf_url') if meta.get(k)), None)
                    # 2) text-based URLs (### URL / md link / bare)
                    urls = [url] if url else []
                    urls += _extract_any_urls(txt)
                    for u in urls:
                        if not u or u in seen:
                            continue
                        seen.add(u)
                        self._last_sources.append({
                            "university": meta.get("university_short"),
                            "document": meta.get("document_type") or "courses",
                            "course": meta.get("course_id"),
                            "section": meta.get("section") or "Programme Structure",
                            "url": u
                        })

                self._last_sources = self._build_sources_for_course(
                    target_course,
                    query_info.get('university'),
                    section=query_info.get('section'),
                )

                stitched = _fix_heading_runs(stitched)
                title = self._build_title(query_info, (
                    full_chunks if ('full_chunks' in locals() and full_chunks) else context_chunks))

                # Direct echo for structure: single markdown block
                yield f"# {title}\n\n{stitched}"

                _summ("ok_structure_exact_echo_full")
                return

        self._last_sources = self._build_sources_for_answer(query_info, context_chunks, limit=6)

        min_threshold = 0.3 if query_info['doc_type'] in ['how_to_apply', 'campus', 'scholarship'] else 0.02
        is_lenient = (
                (query_info['doc_type'] in ['how_to_apply', 'campus', 'scholarship']) or
                (query_info['doc_type'] == 'courses' and bool(query_info.get('section')))
        )
        if not context_chunks or (
                not is_lenient and all(chunk['relevance_score'] < min_threshold for chunk in context_chunks)):
            fallback_text = (
                "I don't have enough information to answer that question. "
                "Please try rephrasing or ask about:\n"
                "• Course programs\n"
                "• Application procedures\n"
                "• Scholarships\n"
                "• Campus locations"
            )
            log_event("not_enough_context", request_id=request_id, returned=len(context_chunks))
            prompt = f"Output EXACTLY the following text and nothing else:\n\n{fallback_text}"
            tried_llm = False
            try:
                t_llm0 = time.time()
                tried_llm = True
                if stream:
                    # --- prepend the big title once ---
                    import re
                    title = self._build_title(query_info, context_chunks)
                    yield f"# {title}\n\n"

                    response = ollama.chat(
                        model=self.model_name,
                        messages=[{'role': 'user', 'content': prompt}],
                        stream=True,
                        keep_alive=LLM_KEEP_ALIVE,
                        options=OLLAMA_OPTIONS
                    )

                    for chunk in response:
                        if 'message' in chunk and 'content' in chunk['message']:
                            yield _postprocess_answer_text(chunk['message']['content'])
                else:
                    response = ollama.chat(
                        model=self.model_name,
                        messages=[{'role': 'user', 'content': prompt}],
                        stream=False,
                        keep_alive=LLM_KEEP_ALIVE,
                        options=OLLAMA_OPTIONS
                    )

                    title = self._build_title(query_info, context_chunks)
                    content = _fix_heading_runs(response['message']['content'])
                    yield f"# {title}\n\n{content}"
            except Exception:
                yield fallback_text
            finally:
                if tried_llm:
                    llm_ms = (time.time() - t_llm0) * 1000
            _summ("no_context")
            return

        # ------------------------------------------------------------------
        # VERITAS special case: fee section is image-only (no RM amounts)
        # ------------------------------------------------------------------
        img_body = self._maybe_build_veritas_fee_image_answer(query_info, context_chunks)
        if img_body is not None:
            # Use the normal pretty title at the top
            title = self._build_title(query_info, context_chunks)

            # Let the LLM echo EXACTLY our markdown (so it still "handles" the answer)
            for piece in self._echo_via_llm_segmented(img_body, title=title, stream=stream):
                yield piece

            # Record last state so sources/feedback still work
            self._last_query = query
            self._last_university_filter = query_info.get("university")
            self._last_sources = self._build_sources_for_answer(query_info, context_chunks, limit=6)
            self._last_course_id = query_info.get("course_id_filter")
            self._last_course_university = query_info.get("university")

            return

        # ---- Special case: TARU/TARUC entry requirements -> echo raw markdown (links/images) ----
        # We want to bypass the LLM and return the exact markdown (so images/PDF links survive).
        is_taru_like = False
        for ch in context_chunks or []:
            meta = ch.get("metadata") or {}
            uni_short = (meta.get("university_short") or "").upper()
            # Accept both TARU and TARUC (and any small spelling variants)
            if uni_short in ("TARU", "TARUC", "TAR UMT", "TAR UMT PENANG"):
                is_taru_like = True
                break

        if (
                query_info.get("doc_type") == "courses"
                and (query_info.get("section") or "").lower().startswith("entry")
                and is_taru_like
        ):
            # Remember sources for /api/chat/sources based on the exact chunks used
            try:
                self._last_sources = _best_course_urls_from_chunks(context_chunks, query_info, limit=4)
            except Exception:
                self._last_sources = []

            # Stitch all markdown chunks for this course/section
            stitched = self._stitch_markdown(context_chunks)
            if not stitched:
                # Fallback: simple join if, for some reason, stitch returns empty
                stitched = "\n\n".join(
                    (c.get("content") or c.get("text") or c.get("document") or "")
                    for c in context_chunks
                )

            # Build the normal title (e.g. "🧾 Entry Requirements — XXX at TARU (...)")
            title = self._build_title(query_info, context_chunks)

            # Echo the markdown EXACTLY (so links/images like
            # ![Minimum Entry Requirements](https://...) are preserved)
            for out in self._echo_via_llm_segmented(stitched, title=title, stream=stream):
                yield out

            return

        prompt = self.build_prompt(query, context_chunks, query_info)

        try:
            log_event("llm_start", request_id=request_id, model=self.model_name, prompt_chars=len(prompt),
                      chunks=len(context_chunks))
            t_llm0 = time.time()
            force_no_stream = is_structure_q
            effective_stream = (stream and not force_no_stream)

            if effective_stream:
                # --- ALWAYS prepend a big title before streaming ---
                title = self._build_title(query_info, context_chunks)
                yield f"# {title}\n\n"

                response = ollama.chat(
                    model=self.model_name,
                    messages=[{'role': 'user', 'content': prompt}],
                    stream=True,
                    keep_alive=LLM_KEEP_ALIVE,
                    options=OLLAMA_OPTIONS
                )
                for chunk in response:
                    if 'message' in chunk and 'content' in chunk['message']:
                        yield _postprocess_answer_text(chunk['message']['content'])

            else:
                response = ollama.chat(
                    model=self.model_name,
                    messages=[{'role': 'user', 'content': prompt}],
                    stream=False,
                    keep_alive=LLM_KEEP_ALIVE,
                    options=OLLAMA_OPTIONS
                )

                content = response['message']['content']
                if is_structure_q:
                    elective_notes = self._extract_elective_notes(context_chunks)
                    content = self._inject_elective_note_under_semester(content, target_semester="Semester 3",
                                                                        notes=elective_notes)
                content = _fix_heading_runs(content)
                title = self._build_title(query_info, context_chunks)
                yield f"# {title}\n\n{content}"

                llm_ms = (time.time() - t_llm0) * 1000
                log_event("llm_done", request_id=request_id, duration_ms=int(llm_ms))
        except Exception as e:
            log_event("llm_error", request_id=request_id, error=str(e))
            yield f"Error generating response: {str(e)}"
            _summ("llm_error", error=str(e))
            return

        _summ("ok")
        return

    # ===================== Sources endpoint (unchanged signature) =====================
    def get_sources(self, query: str, university_filter: Optional[str] = None) -> List[Dict]:
        # Return cached if same query + same university filter
        if (
                self._last_query == query and
                self._last_sources and
                (self._last_university_filter == (university_filter or None))
        ):
            return list(self._last_sources)

        request_id = uuid.uuid4().hex
        log_event(
            "sources_lookup_start",
            request_id=request_id,
            query=query,
            university_filter=university_filter,
        )

        query_info = self.detect_query_type(query)
        if university_filter:
            query_info["university"] = university_filter

        query_lower = query.lower().strip()

        # Use the same retrieved chunks as the answer path
        context_chunks = self.retrieve_context(
            query,
            query_info,
            n_results=3,
            request_id=request_id,
        )

        # Helper: re-build sources using only this course_id from Chroma
        def _salvage_course_sources() -> List[Dict]:
            try:
                cid = next(
                    (
                        (c.get("metadata") or {}).get("course_id")
                        for c in context_chunks
                        if (c.get("metadata") or {}).get("course_id")
                    ),
                    None,
                )
                uni = (
                        query_info.get("university")
                        or next(
                    (
                        (c.get("metadata") or {}).get("university_short")
                        for c in context_chunks
                        if (c.get("metadata") or {}).get("university_short")
                    ),
                    None,
                )
                )

                if not cid:
                    return []

                where_clause = {
                    "$and": [
                        {"document_type": "courses"},
                        {"course_id": cid},
                    ]
                }
                if uni:
                    where_clause["$and"].append({"university_short": uni})

                it = self.collection.get(where=where_clause, include=["documents", "metadatas"])
                docs = it.get("documents") or []
                metas = it.get("metadatas") or []

                full_chunks = [
                    {"content": d, "metadata": (m or {}), "relevance_score": 1.0}
                    for d, m in zip(docs, metas)
                    if d
                ]

                return _best_course_urls_from_chunks(full_chunks, query_info, limit=6)
            except Exception:
                return []

        # First pass: pick URLs directly from retrieved chunks
        sources = _best_course_urls_from_chunks(context_chunks, query_info, limit=6)

        # Detect "subject / modules / programme structure" type questions
        is_subject_question = any(
            key in query_lower
            for key in [
                "subject",
                "subjects",
                "module",
                "modules",
                "programme structure",
                "program structure",
            ]
        )
        section = (query_info.get("section") or "").lower()
        if (
                "subject" in section
                or "module" in section
                or "programme structure" in section
                or "program structure" in section
        ):
            is_subject_question = True

        if query_info.get("doc_type") == "courses":
            # ✅ For subject-type questions, force URL based only on that course_id
            if is_subject_question:
                salvage_sources = _salvage_course_sources()
                if salvage_sources:
                    sources = salvage_sources
            # ✅ For other course questions, keep old behaviour: only salvage if empty
            elif not sources:
                salvage_sources = _salvage_course_sources()
                if salvage_sources:
                    sources = salvage_sources

        log_event("sources_lookup_done", request_id=request_id, count=len(sources))
        return sources

    # ---------- Feedback API ----------
    def submit_feedback(
            self,
            rating: str,
            comment: Optional[str] = None,
            request_id: Optional[str] = None,
    ) -> bool:
        rid = request_id or self._last_request_id
        payload = {
            "event": "feedback",  # event type for the feedback JSON line
            "request_id": rid,
            "rating": "up" if str(rating).lower() in ("up", "1", "true", "yes", "👍") else "down",
            "comment": (comment or "").strip() or None,
            "query": self._last_query,
            "sources": [s for s in (self._last_sources or []) if s.get("url") or s.get("document")],
        }

        try:
            # write one JSON line to feedback_YYYY-MM-DD.jsonl
            save_feedback_line(**payload)
        except Exception as e:
            # if writing fails -> return False and log error
            log_event(
                "feedback_error",
                request_id=rid,
                error=str(e),
            )
            return False

        # For log_event, do NOT pass payload["event"] again
        safe_fields = {k: v for k, v in payload.items() if k not in ("sources", "event")}
        log_event("feedback_saved", **safe_fields)
        return True

    def last_context(self) -> Dict:
        return {
            "request_id": self._last_request_id,
            "query": self._last_query,
            "sources": self._last_sources,
        }


# Singleton instance
_rag_engine = None


def get_rag_engine() -> SmartRAGEngine:
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = SmartRAGEngine(
            db_path=os.getenv('VECTOR_DB_PATH', './vector_db'),
            ollama_url=os.getenv('OLLAMA_URL', 'http://localhost:11434'),
            model_name=os.getenv('LLM_MODEL', 'llama3.2:3b')
        )
    return _rag_engine
