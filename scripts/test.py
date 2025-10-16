#!/usr/bin/env python3
"""
Test Smart RAG System
演示智能检索和回答的完整流程
"""

import sys
import asyncio
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from backend.app.rag_engine import SmartRAGEngine


async def test_query(rag: SmartRAGEngine, query: str, show_full: bool = False):
    """测试单个查询"""
    
    print("\n" + "="*70)
    print(f"❓ QUERY: {query}")
    print("="*70)
    
    response_text = ""
    
    async for chunk in rag.generate_response(query, stream=False):
        response_text += chunk
    
    print(f"\n💬 ANSWER:\n")
    if show_full or len(response_text) < 500:
        print(response_text)
    else:
        print(response_text[:500] + "...")
        print(f"\n[Truncated - full answer is {len(response_text)} characters]")
    
    # Show sources
    sources = rag.get_sources(query)
    print(f"\n📚 SOURCES:")
    for i, source in enumerate(sources, 1):
        source_str = f"  {i}. {source['university']} - {source['document']}"
        if 'course' in source:
            source_str += f" - {source['course']} ({source['section']})"
        source_str += f" [Relevance: {source['relevance']:.1%}]"
        print(source_str)


async def run_test_suite():
    """运行完整测试套件"""
    
    print("="*70)
    print(" Testing Smart RAG System ".center(70))
    print("="*70)
    
    # Initialize RAG engine
    print("\n🚀 Initializing RAG Engine...")
    rag = SmartRAGEngine(
        db_path="./vector_db",
        model_name="llama3.2"
    )
    
    # Test cases organized by category
    test_cases = {
        "📝 APPLICATION QUERIES": [
            "How do I apply to INTI?",
            "What is the application process for ATC?",
            "How to apply to UOW Malaysia?"
        ],
        
        "🏫 CAMPUS QUERIES": [
            "Where is INTI located?",
            "Tell me about ATC campus",
            "What are the UOW campus locations?"
        ],
        
        "💰 SCHOLARSHIP QUERIES": [
            "What scholarships does INTI offer?",
            "ATC scholarship for SPM students",
            "UOW financial aid options"
        ],
        
        "📚 COURSE QUERIES - General": [
            "What courses does INTI offer?",
            "Tell me about diploma programs at UOW",
            "What business programs does ATC have?"
        ],
        
        "📊 COURSE QUERIES - Specific Sections": [
            "What is the programme structure for INTI diploma in business?",
            "How much are the fees for UOW computer science?",
            "Entry requirements for ATC law program",
        ],
        
        "🔍 COMPARISON QUERIES": [
            "Compare scholarships between INTI and UOW",
            "Which university has better business programs?",
        ]
    }
    
    # Run tests
    for category, queries in test_cases.items():
        print(f"\n\n{'#'*70}")
        print(f"# {category}")
        print(f"{'#'*70}")
        
        for query in queries:
            await test_query(rag, query, show_full=False)
            await asyncio.sleep(1)  # Be nice to Ollama
    
    print("\n" + "="*70)
    print(" All Tests Complete! ".center(70))
    print("="*70)
    
    # Summary
    print("\n📊 PERFORMANCE SUMMARY:")
    print(f"   Total documents in DB: {rag.collection.count()}")
    print(f"   Model: {rag.model_name}")
    print(f"   Strategy: Smart document-level + course-section chunking")
    
    print("\n✅ Key Features:")
    print("   • Full document retrieval for simple queries")
    print("   • Section-level retrieval for course queries")
    print("   • Automatic query type detection")
    print("   • Bullet-point formatted answers")
    print("   • Source attribution")


async def interactive_mode():
    """交互模式 - 可以自己提问"""
    
    print("\n" + "="*70)
    print(" Interactive Query Mode ".center(70))
    print("="*70)
    print("\nType your questions (or 'exit' to quit)")
    print("Examples:")
    print("  • How do I apply to INTI?")
    print("  • What scholarships does UOW offer?")
    print("  • Tell me about business programs at ATC")
    print("\n" + "-"*70)
    
    rag = SmartRAGEngine(
        db_path="./vector_db",
        model_name="llama3.2"
    )
    
    while True:
        try:
            query = input("\n❓ Your question: ").strip()
            
            if not query:
                continue
            
            if query.lower() in ['exit', 'quit', 'q']:
                print("\n👋 Goodbye!")
                break
            
            await test_query(rag, query, show_full=True)
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")


def main():
    """主函数"""
    
    import argparse
    
    parser = argparse.ArgumentParser(description='Test Smart RAG System')
    parser.add_argument('--interactive', '-i', action='store_true',
                       help='Run in interactive mode')
    parser.add_argument('--query', '-q', type=str,
                       help='Test a single query')
    
    args = parser.parse_args()
    
    if args.interactive:
        asyncio.run(interactive_mode())
    elif args.query:
        async def single_query():
            rag = SmartRAGEngine(db_path="./vector_db", model_name="llama3.2")
            await test_query(rag, args.query, show_full=True)
        asyncio.run(single_query())
    else:
        asyncio.run(run_test_suite())


if __name__ == "__main__":
    main()