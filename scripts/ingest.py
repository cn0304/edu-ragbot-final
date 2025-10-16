#!/usr/bin/env python3
"""
Smart Data Ingestion - 基于真实使用场景优化
针对不同类型文档采用不同策略
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Tuple
import chromadb
from chromadb.config import Settings


class SmartDocumentProcessor:
    
    def __init__(self):
        self.university_mapping = {
            'Advance Tertiary College': 'ATC',
            'INTI International College': 'INTI',
            'University of Wollongong': 'UOW',
            'Management and Science University': 'MSU',
            'Methodist College Kuala Lumpur': 'MCKL',
            'Penang International Dental College': 'PIDC',
            'Sentral College': 'SENTRAL',
            'The One Academy': 'TOA',
            'UNITAR College': 'UNITAR',
            'Penang Skills Development Centre': 'PSDC',
            'Royal College': 'ROYAL',
            'Veritas College': 'VERITAS',
            'Tunku Abdul Rahman University': 'TARU',
        }
    
    def process_simple_document(self, content: str, university: str, doc_type: str) -> List[Dict]:
        """
        处理简短文档（How to apply, Campus, Scholarship）
        策略：不拆分，整个文档作为一个 chunk
        """
        
        uni_short = self.university_mapping.get(university, university)
        
        # 清理内容
        content = content.strip()
        
        chunk = {
            'content': content,
            'metadata': {
                'university': university,
                'university_short': uni_short,
                'document_type': doc_type,
                'chunk_strategy': 'full_document',
                'is_complete_doc': True
            }
        }
        
        return [chunk]
    
    def process_courses_document(self, content: str, university: str) -> List[Dict]:
        """
        处理课程文档（Courses.md）
        策略：按课程拆分，每个课程的每个 section 是一个 chunk
        
        结构：
        # Diploma / Degree / Certificate (course_type)
        ## course-name (course_id)
        ### Programme Structure (section)
        ### Fee & Intakes (section)
        ### Entry Requirements (section)
        """
        
        uni_short = self.university_mapping.get(university, university)
        chunks = []
        
        # Split by course type at line-start (supports first heading at file start)
        course_type_pattern = r'(?m)^#\s+([^\n]+)\s*\n'
        course_types = re.split(course_type_pattern, content)
        
        # Skip the first empty element
        for i in range(1, len(course_types), 2):
            raw_course_type = course_types[i].strip().lower()
            # Normalize course type, mapping synonyms and multi-word headings
            if ('foundation' in raw_course_type) or ('pre-university' in raw_course_type):
                course_type = 'foundation'
            elif ('degree' in raw_course_type) or ('bachelor' in raw_course_type) or ('undergraduate' in raw_course_type):
                course_type = 'degree'
            elif 'diploma' in raw_course_type:
                course_type = 'diploma'
            elif 'certificate' in raw_course_type:
                course_type = 'certificate'
            elif 'master' in raw_course_type:
                course_type = 'master'
            elif 'phd' in raw_course_type:
                course_type = 'phd'
            else:
                course_type = raw_course_type
            course_type_content = course_types[i + 1] if i + 1 < len(course_types) else ""
            
            # Split by course (## course-name)
            course_pattern = r'(?m)^##\s+([A-Za-z0-9-]+)\s*\n'
            courses = re.split(course_pattern, course_type_content, flags=re.MULTILINE)
            
            # Process each course
            for j in range(1, len(courses), 2):
                course_id = courses[j]  # e.g., "diploma-in-business"
                course_content = courses[j + 1] if j + 1 < len(courses) else ""
                
                # Extract course title from content (first line usually). If missing, derive from slug.
                course_title_match = re.search(r'^([^\n]+)', course_content)
                course_title = None
                if course_title_match:
                    candidate = course_title_match.group(1).strip()
                    if candidate and not candidate.startswith('###'):
                        course_title = candidate
                if not course_title:
                    # Fallback: convert slug to readable title
                    def slug_to_title(slug: str) -> str:
                        base = re.sub(r'[-_]+', ' ', slug).strip()
                        words = []
                        for w in base.split():
                            wl = w.lower()
                            if wl in {'in', 'and', 'of', '&'}:
                                words.append(wl)
                            else:
                                words.append(w.capitalize())
                        return ' '.join(words)
                    course_title = slug_to_title(course_id)
                
                # Split by sections (### Programme Structure, ### Fee & Intakes, etc.)
                section_pattern = r'(?m)^###\s+([^\n]+)\s*\n'
                sections = re.split(section_pattern, course_content, flags=re.MULTILINE)
                
                # First part might contain course overview
                if sections[0].strip():
                    chunk = {
                        'content': sections[0].strip(),
                        'metadata': {
                            'university': university,
                            'university_short': uni_short,
                            'document_type': 'courses',
                            'course_type': course_type,
                            'course_id': course_id,
                            'course_title': course_title,
                            'section': 'Overview',
                            'chunk_strategy': 'course_section'
                        }
                    }
                    chunks.append(chunk)
                
                # Process each section
                for k in range(1, len(sections), 2):
                    section_name = sections[k]  # e.g., "Programme Structure"
                    section_content = sections[k + 1] if k + 1 < len(sections) else ""
                    
                    if section_content.strip():
                        chunk = {
                            'content': section_content.strip(),
                            'metadata': {
                                'university': university,
                                'university_short': uni_short,
                                'document_type': 'courses',
                                'course_type': course_type,
                                'course_id': course_id,
                                'course_title': course_title,
                                'section': section_name,
                                'chunk_strategy': 'course_section'
                            }
                        }
                        chunks.append(chunk)
        
        return chunks
    
    def process_document(self, file_path: Path, university: str) -> List[Dict]:
        """根据文档类型选择处理策略"""
        
        # Read file
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Determine document type
        filename_lower = file_path.name.lower()
        
        if 'course' in filename_lower:
            print(f"    Strategy: Course-level chunking")
            return self.process_courses_document(content, university)
        else:
            # how-to-apply, campus, scholarship
            doc_type = file_path.stem.replace('-', '_').lower()
            # Normalize known typos/synonyms for simple documents
            # Map common variants to canonical types expected by RAG engine
            if doc_type in {"scolarship", "scolarships", "scholarships", "scholar"}:
                doc_type = "scholarship"
            if doc_type in {"how to apply", "how_to_apply", "howtoapply"}:
                doc_type = "how_to_apply"
            if doc_type in {"our campus", "our_campus", "ourcampus", "campus"}:
                doc_type = "campus"
            print(f"    Strategy: Full document (no chunking)")
            return self.process_simple_document(content, university, doc_type)


class SmartDataIngestion:
    """智能数据导入系统"""
    
    def __init__(self, data_dir: str = "./data", db_dir: str = "./vector_db"):
        self.data_dir = Path(data_dir)
        self.db_dir = Path(db_dir)
        self.processor = SmartDocumentProcessor()
        
        # Initialize ChromaDB
        self.client = chromadb.PersistentClient(
            path=str(self.db_dir),
            settings=Settings(anonymized_telemetry=False, allow_reset=True)
        )
        
        # Delete old collection if exists
        try:
            self.client.delete_collection(name="university_docs")
        except:
            pass
        
        # Create new collection
        self.collection = self.client.create_collection(
            name="university_docs",
            metadata={"description": "Smart-chunked university documents"}
        )
        
        print(f"✓ ChromaDB initialized at {self.db_dir}")
    
    def process_university(self, university_path: Path) -> List[Dict]:
        """处理单个大学的所有文档"""
        
        university_name = university_path.name
        print(f"\n📚 Processing: {university_name}")
        
        all_chunks = []
        
        # Process each markdown file
        for md_file in sorted(university_path.glob("*.md")):
            print(f"  📄 {md_file.name}")
            
            chunks = self.processor.process_document(md_file, university_name)
            all_chunks.extend(chunks)
            
            print(f"    → {len(chunks)} chunks created")
        
        return all_chunks
    
    def ingest_all(self):
        """导入所有数据"""
        
        print("🚀 Starting smart data ingestion...")
        
        total_chunks = 0
        stats = {
            'full_documents': 0,
            'course_sections': 0
        }
        
        # Process each university
        for uni_dir in sorted(self.data_dir.iterdir()):
            if uni_dir.is_dir() and not uni_dir.name.startswith('.'):
                chunks = self.process_university(uni_dir)
                
                if chunks:
                    # Count by strategy
                    for chunk in chunks:
                        strategy = chunk['metadata']['chunk_strategy']
                        if strategy == 'full_document':
                            stats['full_documents'] += 1
                        else:
                            stats['course_sections'] += 1
                    
                    # Prepare for ChromaDB
                    documents = [chunk['content'] for chunk in chunks]
                    metadatas = [chunk['metadata'] for chunk in chunks]
                    ids = [f"{i}_{chunk['metadata']['university_short']}_{chunk['metadata']['document_type']}" 
                           for i, chunk in enumerate(chunks, total_chunks)]
                    
                    # Add to database
                    self.collection.add(
                        documents=documents,
                        metadatas=metadatas,
                        ids=ids
                    )
                    
                    total_chunks += len(chunks)
        
        print(f"\n✅ Ingestion complete!")
        print(f"   Total chunks: {total_chunks}")
        print(f"   - Full documents: {stats['full_documents']}")
        print(f"   - Course sections: {stats['course_sections']}")
    
    def test_queries(self):
        
        print("\n" + "="*70)
        print("Testing Query Performance")
        print("="*70)
        
        test_cases = [
            # Simple document queries
            {
                'query': "How do I apply to INTI?",
                'expected_type': 'how_to_apply',
                'university': 'INTI'
            },
            {
                'query': "What scholarships does ATC offer?",
                'expected_type': 'scholarship',
                'university': 'ATC'
            },
            {
                'query': "Where is UOW campus located?",
                'expected_type': 'campus',
                'university': 'UOW'
            },
            # Course queries
            {
                'query': "What diploma programs does INTI have?",
                'expected_type': 'courses',
                'university': 'INTI'
            },
            {
                'query': "INTI computer science program structure",
                'expected_type': 'courses',
                'university': 'INTI',
                'expected_section': 'Programme Structure'
            },
            {
                'query': "UOW business diploma fees",
                'expected_type': 'courses',
                'university': 'UOW',
                'expected_section': 'Fee & Intakes'
            }
        ]
        
        for i, test in enumerate(test_cases, 1):
            print(f"\n{'─'*70}")
            print(f"Test {i}: {test['query']}")
            print(f"Expected: {test['university']} | {test['expected_type']}")
            
            # Build filter
            where_filter = {
                '$and': [
                    {'university_short': test['university']},
                    {'document_type': test['expected_type']}
                ]
            }
            
            # Query
            results = self.collection.query(
                query_texts=[test['query']],
                n_results=3,
                where=where_filter
            )
            
            if results['documents'][0]:
                print(f"\n✓ Found {len(results['documents'][0])} results:")
                
                for j, (doc, meta, dist) in enumerate(zip(
                    results['documents'][0],
                    results['metadatas'][0],
                    results['distances'][0]
                ), 1):
                    relevance = 1 - dist
                    
                    print(f"\n  Result {j}:")
                    print(f"    University: {meta['university_short']}")
                    print(f"    Type: {meta['document_type']}")
                    
                    if 'course_id' in meta:
                        print(f"    Course: {meta['course_id']}")
                        print(f"    Section: {meta['section']}")
                    
                    print(f"    Relevance: {relevance:.1%}")
                    print(f"    Preview: {doc[:150]}...")
            else:
                print("✗ No results found")
    
    def get_document_stats(self):
        """获取数据库统计信息"""
        
        print("\n" + "="*70)
        print("Database Statistics")
        print("="*70)
        
        # Get all documents
        all_docs = self.collection.get()
        
        # Count by university
        uni_counts = {}
        doc_type_counts = {}
        
        for meta in all_docs['metadatas']:
            uni = meta['university_short']
            doc_type = meta['document_type']
            
            uni_counts[uni] = uni_counts.get(uni, 0) + 1
            doc_type_counts[doc_type] = doc_type_counts.get(doc_type, 0) + 1
        
        print("\nBy University:")
        for uni, count in sorted(uni_counts.items()):
            print(f"  {uni}: {count} chunks")
        
        print("\nBy Document Type:")
        for doc_type, count in sorted(doc_type_counts.items()):
            print(f"  {doc_type}: {count} chunks")
        
        print(f"\nTotal: {len(all_docs['ids'])} chunks")


def main():
    """主执行函数"""
    
    print("="*70)
    print(" Smart University RAG Data Ingestion ".center(70))
    print("="*70)
    
    # Initialize and ingest
    ingestion = SmartDataIngestion(
        data_dir="./data",
        db_dir="./vector_db"
    )
    
    # Step 1: Ingest all data
    ingestion.ingest_all()
    
    # Step 2: Show statistics
    ingestion.get_document_stats()
    
    # Step 3: Test queries
    ingestion.test_queries()
    
    print("\n" + "="*70)
    print("✅ All done! You can now start the backend server.")
    print("="*70)


if __name__ == "__main__":
    main()