import os
from typing import List, Dict, Optional, AsyncGenerator
import chromadb
from chromadb.config import Settings
import ollama
import re

try:
    from .reranker import create_reranker, BaseReranker
    RERANKER_AVAILABLE = True
except ImportError:
    RERANKER_AVAILABLE = False
    print("⚠ Reranker not available. Install: pip install sentence-transformers torch")


class SmartRAGEngine:
    """ RAG engine - by searching category """
    
    def __init__(self, 
                 db_path: str = "./vector_db",
                 ollama_url: str = "http://localhost:11434",
                 model_name: str = "llama3.2",
                 use_reranker: bool = True,
                 reranker_strategy: str = "cross-encoder",
                 reranker_model: Optional[str] = None):
        
        # Initialize ChromaDB
        self.client = chromadb.PersistentClient(
            path=db_path,
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_collection(name="university_docs")
        
        # Ollama settings
        self.ollama_url = ollama_url
        self.model_name = model_name
        
        # Initialize Reranker
        self.use_reranker = use_reranker and RERANKER_AVAILABLE
        self.reranker: Optional[BaseReranker] = None
        
        if self.use_reranker:
            try:
                self.reranker = create_reranker(
                    strategy=reranker_strategy,
                    model_name=reranker_model
                )
                print(f"✓ Reranker enabled: {reranker_strategy}")
            except Exception as e:
                print(f"⚠ Failed to initialize reranker: {e}")
                self.use_reranker = False

        print(f"✓ Smart RAG Engine initialized")
        print(f"  - Model: {model_name}")
        print(f"  - Documents: {self.collection.count()}")
        print(f"  - Reranker: {'Enabled' if self.use_reranker else 'Disabled'}")
    
    def detect_query_type(self, query: str) -> Dict:
        """
        检测查询类型和意图
        
        Returns:
            {
                'university': 'INTI' | 'ATC' | 'UOW' | None,
                'doc_type': 'courses' | 'how_to_apply' | 'campus' | 'scholarship' | None,
                'course_query': bool,
                'section': 'Programme Structure' | 'Fee & Intakes' | 'Entry Requirements' | None,
                'list_query': bool,
                'course_type_filter': 'degree' | 'diploma' | 'certificate' | None,
                'degree_subtype': 'doctor' | 'master' | 'bachelor' | None
            }
        """
        
        query_lower = query.lower()
        
        result = {
            'university': None,
            'doc_type': None,
            'course_query': False,
            'section': None,
            'list_query': False,
            'course_type_filter': None
        }
        
        # Detect university
        if 'inti' in query_lower or 'init' in query_lower:
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
        elif 'vertitas' in query_lower or 'vertitas college' in query_lower:
            result['university'] = 'VERTITAS'
        elif 'taru' in query_lower or 'tunku abdul rahman university' in query_lower:
            result['university'] = 'TARU'
        
        # Detect document type
        if any(word in query_lower for word in ['apply', 'application', 'admission', 'register', 'enrol']):
            result['doc_type'] = 'how_to_apply'
        elif any(word in query_lower for word in [
            'scholarship', 'scholarships', 'scholar', 'scolarship',
            'financial aid', 'financial assistance', 'aid', 'bursary', 'award',
            'ptptn', 'edu-assist', 'edu assist', 'study loan', 'loan'
        ]):
            result['doc_type'] = 'scholarship'
        elif any(word in query_lower for word in ['campus', 'location', 'address', 'where is']):
            result['doc_type'] = 'campus'
        elif any(word in query_lower for word in [
            'course', 'program', 'programme', 'study',
            'diploma', 'degree', 'bachelor', 'certificate',
            'foundation', 'pre-university', 'pre university', 'pre-u', 'pre u', 'Pre-University'
        ]):
            result['doc_type'] = 'courses'
            result['course_query'] = True
        
        # Detect section (for course queries)
        if result['course_query']:
            # list-only intent (no details)
            if any(word in query_lower for word in ['offer', 'offers', 'list', 'available', 'have', '课程列表', '有哪些', '提供', '开设']):
                result['list_query'] = True
            # course type filter
            # foundation / pre-university first
            if any(word in query_lower for word in ['foundation', 'pre-university', 'pre university', '预科']):
                result['course_type_filter'] = 'foundation'
            # explicit degree/bachelor (do not force for master/doctor)
            elif any(word in query_lower for word in ['degree', 'bachelor']):
                result['course_type_filter'] = 'degree'
            elif 'diploma' in query_lower:
                result['course_type_filter'] = 'diploma'
            elif 'certificate' in query_lower:
                result['course_type_filter'] = 'certificate'
            # degree subtype
            if any(word in query_lower for word in ['doctor', 'phd', 'doctoral', 'doctorate', '博士']):
                result['degree_subtype'] = 'doctor'
            elif any(word in query_lower for word in ['masters', 'master', 'msc', 'm.sc', 'mba']):
                result['degree_subtype'] = 'master'
            elif 'bachelor' in query_lower:
                result['degree_subtype'] = 'bachelor'
            if any(word in query_lower for word in ['structure', 'curriculum', 'subject', 'module', 'learn']):
                result['section'] = 'Programme Structure'
            # Prefer explicit fees vs intakes separation to match data sections
            elif any(word in query_lower for word in ['fee', 'fees', 'cost', 'price', 'tuition']):
                result['section'] = 'Fees'
            elif any(word in query_lower for word in ['intake', 'intakes', 'start']):
                # Many course files use 'Campus Intakes' for intake info
                result['section'] = 'Campus Intakes'
            elif any(word in query_lower for word in ['entry', 'requirement', 'qualification', 'eligibility', 'spm', 'stpm', 'a-level']):
                result['section'] = 'Entry Requirements'

        # General list intent for non-course documents (e.g., scholarships)
        if result['doc_type'] in ['scholarship'] and not result['course_query']:
            if any(word in query_lower for word in ['list', 'lists', 'offer', 'offers', 'available', 'types', 'all']):
                result['list_query'] = True

        # Fallback: handle intake-only phrasing even if course keywords weren’t detected
        # e.g., "ATC foundation-in-law intake period" should still be treated as a course query
        if not result['doc_type'] and any(word in query_lower for word in ['intake', 'intakes', 'start']):
            result['doc_type'] = 'courses'
            result['course_query'] = True
            # Default to Campus Intakes to align with data sections
            result['section'] = 'Campus Intakes'
        
        return result

    def retrieve_context(self, query: str, query_info: Dict, n_results: int = 3) -> List[Dict]:
        """
        Retrieve candidate chunks from Chroma and (optionally) rerank them.
        """
        # ---------- Build Chroma where clause (unchanged) ----------
        conditions = []

        if query_info['university']:
            conditions.append({'university_short': query_info['university']})

        if query_info['doc_type']:
            conditions.append({'document_type': query_info['doc_type']})

        if query_info['section']:
            section = query_info['section']
            synonyms_map = {
                'Programme Structure': [
                    'Programme Structure', 'Program Structure',
                    'Structure', 'Course Structure',
                    'Curriculum', 'Modules', 'Subjects'
                ],
                'Fees': ['Fees', 'Fee', 'Fee & Intakes', 'Tuition'],
                'Fee & Intakes': ['Fee & Intakes', 'Fees', 'Fee', 'Intakes', 'Campus Intakes'],
                'Fee': ['Fee', 'Fees', 'Fee & Intakes', 'Tuition'],
                'Entry Requirements': ['Entry Requirements', 'Requirements', 'Entry Requirement'],
                'Campus Intakes': ['Campus Intakes', 'Intakes', 'Fee & Intakes'],
                'Intakes': ['Intakes', 'Campus Intakes', 'Fee & Intakes']
            }
            candidates = synonyms_map.get(section, [section])
            if len(candidates) == 1:
                conditions.append({'section': candidates[0]})
            else:
                conditions.append({'$or': [{'section': s} for s in candidates]})

        if query_info.get('course_id_filter'):
            conditions.append({'course_id': query_info['course_id_filter']})

        if query_info.get('course_type_filter'):
            conditions.append({'course_type': query_info['course_type_filter']})

        if not conditions:
            where_clause = None
        elif len(conditions) == 1:
            where_clause = conditions[0]
        else:
            where_clause = {'$and': conditions}

        # ---------- Over-fetch so the reranker has room to work ----------
        retrieval_k = n_results
        if self.use_reranker:
            retrieval_k = max(n_results * 4, 12)  # e.g., 12 for top-3

        results = self.collection.query(
            query_texts=[query],
            n_results=retrieval_k,
            where=where_clause
        )

        # ---------- Format results ----------
        context_chunks: List[Dict] = []
        for doc, meta, dist in zip(
                results.get('documents', [[]])[0],
                results.get('metadatas', [[]])[0],
                results.get('distances', [[]])[0]
        ):
            # Convert distance to a similarity in [0,1]
            if dist is None:
                sim = 0.0
            else:
                d = float(dist)
                if 0.0 <= d <= 2.0:  # cosine distance range
                    sim = 1.0 - (d / 2.0)  # 0 -> 1.0, 2 -> 0.0
                else:
                    sim = 1.0 - d  # fallback for other metrics
                sim = max(0.0, min(1.0, sim))  # clamp

            context_chunks.append({
                'content': doc,
                'metadata': meta,
                'relevance_score': sim
            })

        # ---------- Rerank (now it actually runs) ----------
        if self.use_reranker and self.reranker and len(context_chunks) >= 2:
            print(f"   🔄 Reranking {len(context_chunks)} → {n_results} chunks")
            context_chunks = self.reranker.rerank(
                query=query,
                documents=context_chunks,
                top_k=n_results
            )
            # Already trimmed by reranker
            return context_chunks

        # ---------- Fallbacks (no reranker or too few hits) ----------
        try:
            if len(context_chunks) < n_results and where_clause is not None:
                items = self.collection.get(where=where_clause, include=['documents', 'metadatas'])
                docs = items.get('documents', [])
                metas = items.get('metadatas', [])
                seen = {c['content'] for c in context_chunks}
                for d, m in zip(docs, metas):
                    if d and d not in seen:
                        context_chunks.append({
                            'content': d,
                            'metadata': m,
                            'relevance_score': 0.5
                        })
                        seen.add(d)
                        if len(context_chunks) >= n_results:
                            break
        except Exception:
            pass

        # Final trim
        return context_chunks[:n_results]

    def _match_course(self, query: str, university_short: Optional[str]) -> Optional[Dict[str, str]]:
        """Find best matching course (id/title) from the collection for a given query and university.
        Returns { 'course_id': str, 'course_title': str } or None.
        """
        # Prepare where clause
        conditions = [{'document_type': 'courses'}]
        if university_short:
            conditions.append({'university_short': university_short})

        # Build proper where clause
        if not conditions:
            where_clause = None
        elif len(conditions) == 1:
            where_clause = conditions[0]
        else:
            where_clause = {'$and': conditions}

        items = self.collection.get(where=where_clause, include=['metadatas'])
        metadatas = items.get('metadatas', [])
        if not metadatas:
            return None

        q = query.lower()
        # Tokenize and drop common words
        tokens = re.findall(r"[a-zA-Z0-9]+", q)
        stop = {
            'what','is','the','program','programme','course','structure','fee','fees','intakes',
            'entry','requirements','requirement','about','and','of','in','at','for','name'
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
            # Score by overlap
            overlap = len(set(q_tokens) & set(c_tokens))
            # Boost exact substring matches
            if base and any(w in base for w in q_tokens):
                overlap += 1
            if overlap > best_score:
                best_score = overlap
                best = {
                    'course_id': meta.get('course_id'),
                    'course_title': meta.get('course_title') or cid.replace('-', ' ').title()
                }
        # Require minimal confidence
        if best and best_score >= 1:
            return best
        return None

    def list_courses(self, query_info: Dict) -> List[str]:
        """列出课程名称（仅名称，不含详情）"""
        # 使用 $and 组合过滤条件
        conditions = [{'document_type': 'courses'}]

        if query_info.get('university'):
            conditions.append({'university_short': query_info['university']})
        # 对 master/doctor 子类型，不预先用 course_type=degree 过滤，避免漏抓
        apply_type_filter = True
        if query_info.get('course_type_filter') == 'degree' and query_info.get('degree_subtype') in ('master', 'doctor'):
            apply_type_filter = False
        if apply_type_filter and query_info.get('course_type_filter'):
            conditions.append({'course_type': query_info['course_type_filter']})

        try:
            # Build proper where clause for listing: avoid $and when only one condition
            if not conditions:
                where_clause = None
            elif len(conditions) == 1:
                where_clause = conditions[0]
            else:
                where_clause = {'$and': conditions}
            items = self.collection.get(where=where_clause, include=['metadatas'])
            titles = []
            for meta in items.get('metadatas', []):
                # 优先使用 course_id（slug）并格式化为可读名称；
                # 若没有，则使用 course_title，且跳过像 '### Programme Structure' 这样的小节标题
                raw_id = meta.get('course_id')
                raw_title = meta.get('course_title')

                title: Optional[str] = None
                if raw_id:
                    # slug -> Title Case
                    title = raw_id.replace('-', ' ').strip().title()
                elif raw_title:
                    t = raw_title.strip()
                    if t.startswith('###'):
                        # 跳过误识别的小节标题
                        continue
                    title = t

                if title:
                    titles.append(title)
            # 去重并保持顺序
            seen = set()
            unique_titles = []
            for t in titles:
                if t not in seen:
                    seen.add(t)
                    unique_titles.append(t)

            # 根据学位子类型进行二次过滤（doctor/master/bachelor）
            subtype = query_info.get('degree_subtype')
            if query_info.get('course_type_filter') == 'degree' and subtype:
                def match_subtype(name: str, st: str) -> bool:
                    n = name.lower()
                    if st == 'doctor':
                        return any(k in n for k in ['doctor', 'phd', 'ph.d', 'doctoral', 'doctorate'])
                    if st == 'master':
                        return any(k in n for k in ['master', 'msc', 'm.sc', 'mba']) and not any(k in n for k in ['bachelor'])
                    if st == 'bachelor':
                        return 'bachelor' in n or any(k in n for k in ['b.sc', 'bsc', 'b.eng', 'beng', 'b.a', 'ba'])
                    return True
                unique_titles = [t for t in unique_titles if match_subtype(t, subtype)]
            # Foundation 追加基于标题的语义过滤（即使元数据缺失也能筛中）
            if query_info.get('course_type_filter') == 'foundation':
                lt = []
                for t in unique_titles:
                    tl = t.lower()
                    if 'foundation' in tl or 'pre-university' in tl or 'pre university' in tl:
                        lt.append(t)
                unique_titles = lt

            # 根据学位子类型进行二次过滤（doctor/master/bachelor）
            subtype = query_info.get('degree_subtype')
            if query_info.get('course_type_filter') in (None, 'degree') and subtype:
                def match_subtype(name: str, st: str) -> bool:
                    n = name.lower()
                    if st == 'doctor':
                        return any(k in n for k in ['doctor', 'phd', 'ph.d', 'doctoral', 'doctorate'])
                    if st == 'master':
                        return any(k in n for k in ['master', 'msc', 'm.sc', 'mba']) and not any(k in n for k in ['bachelor'])
                    if st == 'bachelor':
                        return 'bachelor' in n or any(k in n for k in ['b.sc', 'bsc', 'b.eng', 'beng', 'b.a', 'ba'])
                    return True
                unique_titles = [t for t in unique_titles if match_subtype(t, subtype)]
            return unique_titles
        except Exception:
            return []
    
    def build_prompt(self, query: str, context_chunks: List[Dict], query_info: Dict) -> str:
        """构建针对性的 prompt"""
        
        # Build context
        context_text = ""
        for i, chunk in enumerate(context_chunks, 1):
            meta = chunk['metadata']
            
            context_text += f"\n{'='*60}\n"
            context_text += f"[Source {i}: {meta['university_short']} - {meta['document_type']}"
            
            if 'course_id' in meta:
                context_text += f" - {meta.get('course_title', meta['course_id'])}"
                if meta.get('section'):
                    context_text += f" - {meta['section']}"
            
            context_text += "]\n"
            context_text += f"{'='*60}\n\n"
            context_text += chunk['content']
            context_text += "\n\n"
        
        # Determine answer style based on query type
        if query_info['doc_type'] == 'courses' and query_info['section']:
            # Specific course section query
            answer_instruction = """
Present the answer in a clear, organized bullet-point format:
- Use bullet points (•) for main items
- Use sub-bullets (  -) for details
- Keep it concise but complete
- Include all important information (dates, amounts, requirements, etc.)
"""
        elif query_info['doc_type'] in ['how_to_apply', 'scholarship', 'campus']:
            # Simple document query
            answer_instruction = """
Present the answer in a clear, organized format:
- Use bullet points for lists
- Use numbered steps for procedures
- Include all relevant details (contact info, addresses, amounts, dates)
- Be complete - don't summarize unless asked
"""
        else:
            # General query
            answer_instruction = """
Present the answer in a clear, organized bullet-point format.
Include all relevant information from the sources.
"""
        
        # Build prompt
        prompt = f"""You are a helpful university admission assistant for Malaysian universities.

CONTEXT INFORMATION:
{context_text}

USER QUESTION: {query}

INSTRUCTIONS:
1. Answer ONLY based on the context information provided above
2. If the answer is not in the context, say "I don't have that information"
3. Always mention which university you're referring to
4. {answer_instruction}
5. Be accurate - include specific numbers, dates, requirements, contact details
6. If comparing multiple universities, organize by university

YOUR ANSWER:"""
        
        return prompt
    
    async def generate_response(self, 
                               query: str,
                               university_filter: Optional[str] = None,
                               stream: bool = True) -> AsyncGenerator[str, None]:
        """
        生成响应
        """
        
        # Step 1: Detect query type
        query_info = self.detect_query_type(query)
        # External university filter override
        if university_filter:
            query_info['university'] = university_filter

        # For course queries, try matching a specific program and enforce specificity
        if query_info.get('doc_type') == 'courses':
            best = self._match_course(query, query_info.get('university'))
            if best:
                query_info['course_id_filter'] = best['course_id']
            # If neither university nor course can be determined, ask for specificity
            if not query_info.get('university') and not query_info.get('course_id_filter'):
                msg = (
                    "Please specify both the university (e.g., INTI) and the program name "
                    "(e.g., Computer Science) so I can retrieve the Programme Structure, "
                    "Fee & Intakes, or Entry Requirements sections."
                )
                yield msg
                return
        
        print(f"\n🔍 Query Analysis:")
        print(f"   University: {query_info['university'] or 'All'}")
        print(f"   Doc Type: {query_info['doc_type'] or 'Any'}")
        print(f"   Section: {query_info['section'] or 'N/A'}")

        # Special path: list-only course query (no details) — route through LLM
        if query_info['doc_type'] == 'courses' and query_info['course_query'] and query_info.get(
                'list_query') and not query_info.get('section'):
            titles = self.list_courses(query_info)

            # Build a minimal "context" from the list so LLM formats it nicely
            uni = query_info.get('university') or 'All'
            if titles:
                list_context = "\n".join([f"- {t}" for t in titles])
                prompt = f"""You are a helpful university admission assistant.

        CONTEXT INFORMATION:
        University: {uni}
        Detected intent: course list
        Courses:
        {list_context}

        USER QUESTION: {query}

        INSTRUCTIONS:
        Return a clean, concise bullet list of the courses above.
        - Do NOT add or invent courses.
        - If a university is specified, mention it in the header.
        - No extra commentary beyond the list and a short header.
        """

                try:
                    if stream:
                        response = ollama.chat(
                            model=self.model_name,
                            messages=[{'role': 'user', 'content': prompt}],
                            stream=True
                        )
                        for chunk in response:
                            if 'message' in chunk and 'content' in chunk['message']:
                                yield chunk['message']['content']
                    else:
                        response = ollama.chat(
                            model=self.model_name,
                            messages=[{'role': 'user', 'content': prompt}],
                            stream=False
                        )
                        yield response['message']['content']
                except Exception as e:
                    # Hard fallback if LLM fails
                    header = f"Course list — {uni}"
                    yield header + ":\n" + "\n".join([f"• {t}" for t in titles])
                return

            # No titles case — still go through LLM (echo the same message)
            header = "No courses found"
            if query_info.get('university'):
                header += f" for {query_info['university']}"
            if query_info.get('course_type_filter'):
                header += f" [{query_info['course_type_filter'].title()}]"
            if query_info.get('degree_subtype'):
                header += f" - {query_info['degree_subtype'].title()}"
            fallback_text = header + ". Try adjusting filters (e.g., 'master', 'bachelor')."

            prompt = f"Return exactly this sentence without adding anything else:\n\n{fallback_text}"
            try:
                if stream:
                    response = ollama.chat(
                        model=self.model_name,
                        messages=[{'role': 'user', 'content': prompt}],
                        stream=True
                    )
                    for chunk in response:
                        if 'message' in chunk and 'content' in chunk['message']:
                            yield chunk['message']['content']
                else:
                    response = ollama.chat(
                        model=self.model_name,
                        messages=[{'role': 'user', 'content': prompt}],
                        stream=False
                    )
                    yield response['message']['content']
            except Exception:
                yield fallback_text
            return

        # Step 2: Retrieve context
        # For full documents, we only need 1 result; for course sections fetch more
        if query_info['doc_type'] in ['how_to_apply', 'campus', 'scholarship']:
            n_results = 1
        else:
            n_results = 6
        
        context_chunks = self.retrieve_context(query, query_info, n_results=n_results)
        
        # Thresholding: be lenient for simple docs and course section queries
        # Rationale: full documents like how_to_apply/campus/scholarship should be used even with low similarity
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
            prompt = f"Output EXACTLY the following text and nothing else:\n\n{fallback_text}"
            try:
                if stream:
                    response = ollama.chat(
                        model=self.model_name,
                        messages=[{'role': 'user', 'content': prompt}],
                        stream=True
                    )
                    for chunk in response:
                        if 'message' in chunk and 'content' in chunk['message']:
                            yield chunk['message']['content']
                else:
                    response = ollama.chat(
                        model=self.model_name,
                        messages=[{'role': 'user', 'content': prompt}],
                        stream=False
                    )
                    yield response['message']['content']
            except Exception:
                # Last resort fallback
                yield fallback_text
            return

        print(f"   Retrieved: {len(context_chunks)} chunks")
        print(f"   Top relevance: {context_chunks[0]['relevance_score']:.1%}")
        
        # Step 3: Build prompt
        prompt = self.build_prompt(query, context_chunks, query_info)
        
        # Step 4: Generate with Ollama
        try:
            if stream:
                response = ollama.chat(
                    model=self.model_name,
                    messages=[{'role': 'user', 'content': prompt}],
                    stream=True
                )
                
                for chunk in response:
                    if 'message' in chunk and 'content' in chunk['message']:
                        yield chunk['message']['content']
            else:
                response = ollama.chat(
                    model=self.model_name,
                    messages=[{'role': 'user', 'content': prompt}],
                    stream=False
                )
                yield response['message']['content']
        except Exception as e:
            yield f"Error generating response: {str(e)}"

    def get_sources(self, query: str, university_filter: Optional[str] = None) -> List[Dict]:
        query_info = self.detect_query_type(query)
        if university_filter:
            query_info['university'] = university_filter
        # keep n_results=3 or as-is
        context_chunks = self.retrieve_context(query, query_info, n_results=3)

        sources = []
        # ... inside SmartRAGEngine.get_sources(), in the for-loop over context_chunks:

        for chunk in context_chunks:
            meta = chunk['metadata']
            vector_sim = float(chunk.get('relevance_score', 0.0))
            rerank = chunk.get('rerank_score')

            primary = float(rerank) if rerank is not None else vector_sim

            # inside get_sources(), inside the for chunk in context_chunks: loop
            src = {
                'university': meta['university_short'],
                'document': meta['document_type'],
                'relevance': round(primary, 3),
                'vector_similarity': round(vector_sim, 3),
            }
            if rerank is not None:
                src['rerank_score'] = round(float(rerank), 3)

            # 🔗 add this block
            url = None
            # 1) prefer explicit metadata if present
            for key in ('url', 'source_url', 'page_url', 'pdf_url'):
                if key in meta and meta[key]:
                    url = meta[key]
                    break
            # 2) otherwise, grab the first link from the chunk text (Markdown or bare URL)
            if not url:
                text = chunk.get('content', '')
                m = re.search(r'\((https?://[^\s)]+)\)', text) or re.search(r'https?://[^\s)]+', text)
                if m:
                    url = m.group(1)
            if url:
                src['url'] = url

            # keep your existing extras
            if 'course_id' in meta:
                src['course'] = meta['course_id']
                src['section'] = meta.get('section', 'Overview')

            sources.append(src)

        return sources


# Singleton instance
_rag_engine = None

def get_rag_engine() -> SmartRAGEngine:
    """Get or create RAG engine instance"""
    global _rag_engine
    if _rag_engine is None:
        _rag_engine = SmartRAGEngine(
            db_path=os.getenv('VECTOR_DB_PATH', './vector_db'),
            ollama_url=os.getenv('OLLAMA_URL', 'http://localhost:11434'),
            model_name=os.getenv('LLM_MODEL', 'llama3.2')
        )
    return _rag_engine