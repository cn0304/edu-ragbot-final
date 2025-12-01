#!/usr/bin/env python3
"""
Reranker module for improving retrieval quality
Supports multiple reranking strategies
"""

from typing import List, Dict, Any, Optional
from sentence_transformers import CrossEncoder
import numpy as np
from abc import ABC, abstractmethod


class BaseReranker(ABC):
    """Base class for rerankers"""
    
    @abstractmethod
    def rerank(self, query: str, documents: List[Dict], top_k: int = 5) -> List[Dict]:
        """
        Rerank documents based on query relevance
        """
        pass


class CrossEncoderReranker(BaseReranker):
    """
    Cross-encoder based reranker
    More accurate than bi-encoder but slower
    """
    
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        """
        Initialize cross-encoder reranker
        
        Popular models:
        - cross-encoder/ms-marco-MiniLM-L-6-v2 (Fast, English)
        - cross-encoder/ms-marco-MiniLM-L-12-v2 (Better quality, English)
        - BAAI/bge-reranker-base (Multilingual, supports Chinese)
        - BAAI/bge-reranker-large (Best quality, slower)
        """
        print(f"Loading reranker model: {model_name}")
        try:
            self.model = CrossEncoder(model_name, max_length=512)
            self.model_name = model_name
            print(f"✓ Reranker loaded successfully")
        except Exception as e:
            print(f"⚠ Failed to load reranker: {e}")
            print("  Falling back to simple reranker")
            self.model = None
    
    def rerank(self, query: str, documents: List[Dict], top_k: int = 5) -> List[Dict]:
        """
        Rerank documents using cross-encoder
        """
        if not documents:
            return []
        
        if self.model is None:
            # Fallback: return top_k documents based on original scores
            return documents[:top_k]
        
        # Prepare query-document pairs
        pairs = [[query, doc['content']] for doc in documents]
        
        # Get reranking scores
        try:
            scores = self.model.predict(pairs)
            
            # Update documents with new scores
            for doc, score in zip(documents, scores):
                doc['rerank_score'] = float(score)
                doc['original_score'] = doc.get('relevance_score', 0.0)
            
            # Sort by rerank score
            reranked = sorted(documents, key=lambda x: x['rerank_score'], reverse=True)
            
            return reranked[:top_k]
            
        except Exception as e:
            print(f"⚠ Reranking failed: {e}")
            return documents[:top_k]


class BGEReranker(BaseReranker):
    """
    BGE (BAAI General Embedding) Reranker
    Excellent for multilingual scenarios (Chinese + English)
    """
    
    def __init__(self, model_name: str = "BAAI/bge-reranker-base"):
        """
        Initialize BGE reranker
        
        Models:
        - BAAI/bge-reranker-base (Balanced)
        - BAAI/bge-reranker-large (Best quality)
        """
        print(f"Loading BGE reranker: {model_name}")
        try:
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(model_name, max_length=512)
            self.model_name = model_name
            print(f"✓ BGE Reranker loaded")
        except Exception as e:
            print(f"⚠ Failed to load BGE reranker: {e}")
            self.model = None
    
    def rerank(self, query: str, documents: List[Dict], top_k: int = 5) -> List[Dict]:
        """
        Rerank using BGE model
        """
        if not documents or self.model is None:
            return documents[:top_k]
        
        pairs = [[query, doc['content']] for doc in documents]
        
        try:
            scores = self.model.predict(pairs)
            
            for doc, score in zip(documents, scores):
                doc['rerank_score'] = float(score)
                doc['original_score'] = doc.get('relevance_score', 0.0)
            
            reranked = sorted(documents, key=lambda x: x['rerank_score'], reverse=True)
            return reranked[:top_k]
            
        except Exception as e:
            print(f"⚠ BGE reranking failed: {e}")
            return documents[:top_k]


class HybridReranker(BaseReranker):
    """
    Hybrid reranker combining multiple signals
    """
    
    def __init__(self, 
                 cross_encoder_weight: float = 0.7,
                 vector_score_weight: float = 0.3):
        """
        Combine cross-encoder score with original vector similarity
        """
        self.ce_weight = cross_encoder_weight
        self.vector_weight = vector_score_weight
        
        # Try to load cross-encoder
        try:
            self.cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            print("✓ Hybrid reranker with cross-encoder loaded")
        except Exception as e:
            print(f"⚠ Cross-encoder failed, using vector-only: {e}")
            self.cross_encoder = None
    
    def rerank(self, query: str, documents: List[Dict], top_k: int = 5) -> List[Dict]:
        """
        Hybrid reranking
        """
        if not documents:
            return []
        
        if self.cross_encoder is None:
            # Fallback to vector scores only
            return sorted(documents, 
                         key=lambda x: x.get('relevance_score', 0.0), 
                         reverse=True)[:top_k]
        
        pairs = [[query, doc['content']] for doc in documents]
        
        try:
            ce_scores = self.cross_encoder.predict(pairs)
            
            for doc, ce_score in zip(documents, ce_scores):
                vector_score = doc.get('relevance_score', 0.0)
                
                # Normalize scores to [0, 1]
                ce_score_norm = (ce_score + 10) / 20  # Rough normalization
                ce_score_norm = max(0, min(1, ce_score_norm))
                
                # Hybrid score
                hybrid_score = (
                    self.ce_weight * ce_score_norm +
                    self.vector_weight * vector_score
                )
                
                doc['rerank_score'] = float(hybrid_score)
                doc['ce_score'] = float(ce_score)
                doc['original_score'] = vector_score
            
            reranked = sorted(documents, key=lambda x: x['rerank_score'], reverse=True)
            return reranked[:top_k]
            
        except Exception as e:
            print(f"⚠ Hybrid reranking failed: {e}")
            return documents[:top_k]


class SimpleReranker(BaseReranker):
    """
    Simple keyword-based reranker (fallback)
    Fast but less accurate
    """
    
    def rerank(self, query: str, documents: List[Dict], top_k: int = 5) -> List[Dict]:
        """
        Simple keyword overlap reranking
        """
        if not documents:
            return []
        
        query_tokens = set(query.lower().split())
        
        for doc in documents:
            content_tokens = set(doc['content'].lower().split())
            overlap = len(query_tokens & content_tokens)
            doc['keyword_score'] = overlap / max(len(query_tokens), 1)
            
            # Combine with original score
            original = doc.get('relevance_score', 0.0)
            doc['rerank_score'] = 0.6 * original + 0.4 * doc['keyword_score']
        
        reranked = sorted(documents, key=lambda x: x['rerank_score'], reverse=True)
        return reranked[:top_k]


def create_reranker(
    strategy: str = "cross-encoder",
    model_name: Optional[str] = None
) -> BaseReranker:
    """
    Factory function to create reranker
    
    Args:
        strategy: Reranking strategy
            - "cross-encoder": Fast cross-encoder (default)
            - "bge": BGE reranker (better for Chinese)
            - "hybrid": Hybrid approach
            - "simple": Keyword-based (fallback)
        model_name: Optional specific model name
    
    Returns:
        Reranker instance
    """
    if strategy == "bge":
        model = model_name or "BAAI/bge-reranker-base"
        return BGEReranker(model_name=model)
    
    elif strategy == "hybrid":
        return HybridReranker()
    
    elif strategy == "simple":
        return SimpleReranker()
    
    else:  # "cross-encoder" (default)
        model = model_name or "cross-encoder/ms-marco-MiniLM-L-6-v2"
        return CrossEncoderReranker(model_name=model)


# Performance benchmarking
class RerankerBenchmark:
    """Benchmark different reranking strategies"""
    
    def __init__(self):
        self.rerankers = {
            'cross-encoder': create_reranker('cross-encoder'),
            'simple': create_reranker('simple')
        }
        
        # Try to load BGE if available
        try:
            self.rerankers['bge'] = create_reranker('bge')
        except:
            pass
    
    def benchmark(self, query: str, documents: List[Dict], top_k: int = 5) -> Dict[str, Any]:
        """
        Benchmark all rerankers
        """
        import time
        
        results = {}
        
        for name, reranker in self.rerankers.items():
            start = time.time()
            reranked = reranker.rerank(query, documents.copy(), top_k)
            elapsed = time.time() - start
            
            results[name] = {
                'time': elapsed,
                'top_scores': [doc.get('rerank_score', 0) for doc in reranked[:3]]
            }
        
        return results