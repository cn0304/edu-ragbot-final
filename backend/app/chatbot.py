#!/usr/bin/env python3
"""
Chatbot API endpoints for RAG system
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json
import asyncio
from fastapi.responses import JSONResponse
import traceback
from .rag_engine import get_rag_engine


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """Chat request model"""
    query: str
    university_filter: Optional[str] = None
    stream: bool = True


class ChatResponse(BaseModel):
    """Chat response model"""
    answer: str
    sources: List[Dict[str, Any]]
    query_info: Dict[str, Any]


class SourceRequest(BaseModel):
    """Request to get sources for a query"""
    query: str
    university_filter: Optional[str] = None

class FeedbackRequest(BaseModel):
    rating: str                    # "up" or "down"
    comment: Optional[str] = None  # optional short text
    request_id: Optional[str] = None  # optional; falls back to last request


@router.post("/query")
async def chat_query(request: ChatRequest):
    """
    Chat endpoint with streaming support
    """
    try:
        rag = get_rag_engine()
        
        if request.stream:
            # Streaming response
            async def generate():
                try:
                    async for chunk in rag.generate_response(
                        query=request.query,
                        university_filter=request.university_filter,
                        stream=True
                    ):
                        # Send each chunk as SSE (Server-Sent Events)
                        yield f"data: {json.dumps({'chunk': chunk, 'done': False})}\n\n"
                    
                    # Send completion signal
                    yield f"data: {json.dumps({'chunk': '', 'done': True})}\n\n"
                    
                except Exception as e:
                    yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"
            
            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no"
                }
            )
        else:
            # Non-streaming response
            answer = ""
            async for chunk in rag.generate_response(
                query=request.query,
                university_filter=request.university_filter,
                stream=False
            ):
                answer += chunk
            
            # Get sources
            sources = rag.get_sources(request.query, request.university_filter)
            
            # Get query info
            query_info = rag.detect_query_type(request.query)
            if request.university_filter:
                query_info['university'] = request.university_filter
            
            return ChatResponse(
                answer=answer,
                sources=sources,
                query_info=query_info
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sources")
async def get_sources(request: SourceRequest):
    """
    Get sources for a query without generating answer
    """
    try:
        rag = get_rag_engine()
        sources = rag.get_sources(request.query, request.university_filter)
        
        return {
            "sources": sources,
            "count": len(sources)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/feedback")
async def chat_feedback(payload: FeedbackRequest):
    """
    Store simple thumbs-up/down feedback with optional comment.
    Always return a JSON object so the UI doesn't see a network error.
    """
    rag = get_rag_engine()
    try:
        rag.submit_feedback(
            rating=payload.rating,
            comment=payload.comment,
            request_id=payload.request_id,
        )
        # success
        return {"ok": True}
    except Exception as e:
        # Log full traceback to server console for debugging
        tb = traceback.format_exc()
        print("[/feedback] ERROR:", e, "\n", tb)
        # Return 200 + ok:false so the frontend doesn't show a red 'network error'
        # (You can change to status_code=500 if you prefer strict behavior)
        return JSONResponse(status_code=200, content={"ok": False, "error": str(e)})

@router.get("/universities")
async def get_universities():
    """
    Get list of available universities
    """
    return {
        "universities": [
            {"code": "INTI", "name": "INTI International University"},
            {"code": "ATC", "name": "Advance Tertiary College"},
            {"code": "UOW", "name": "University of Wollongong Malaysia"},
            {"code": "MSU", "name": "Management & Science University"},
            {"code": "MCKL", "name": "Methodist College Kuala Lumpur"},
            {"code": "PIDC", "name": "Penang International Dental College"},
            {"code": "SENTRAL", "name": "Sentral College"},
            {"code": "TOA", "name": "The One Academy"},
            {"code": "UNITAR", "name": "UNITAR College"},
            {"code": "PSDC", "name": "Penang Skills Development Centre"},
            {"code": "ROYAL", "name": "Royal College"},
            {"code": "VERITAS", "name": "Veritas College"},
            {"code": "TARU", "name": "Tunku Abdul Rahman University"}
        ]
    }


@router.get("/health")
async def health_check():
    """
    Health check endpoint
    """
    try:
        rag = get_rag_engine()
        doc_count = rag.collection.count()
        
        return {
            "status": "healthy",
            "model": rag.model_name,
            "documents": doc_count,
            "ollama_url": rag.ollama_url
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }