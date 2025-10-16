import json
import os
import time
from typing import Dict, Any

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles
except Exception:
    FastAPI = None  # type: ignore

import sys
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent.parent.parent / 'backend'
sys.path.insert(0, str(backend_path))

from .rag_engine import SmartRAGEngine


def read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def build_system_metrics() -> Dict[str, Any]:
    rag = SmartRAGEngine()
    doc_count = rag.collection.count()
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data')
    colleges = []
    for name in sorted(os.listdir(data_dir)):
        base = os.path.join(data_dir, name)
        if not os.path.isdir(base):
            continue
        outputs = {}
        for out in ('Courses.md', 'Scholarship.md', 'How to apply.md', 'Our Campus.md'):
            p = os.path.join(base, out)
            outputs[out] = os.path.isfile(p)
        inputs = {}
        inp = os.path.join(base, 'input.txt')
        inputs['input.txt'] = os.path.isfile(inp)
        pdfs = os.path.join(base, 'pdfs')
        inputs['pdfs_dir'] = (name == 'Peninsula College') and os.path.isdir(pdfs)
        colleges.append({
            'name': name,
            'outputs': outputs,
            'inputs': inputs,
        })
    return {
        'timestamp': int(time.time()),
        'vector_db': {
            'documents': doc_count
        },
        'data_sources': colleges
    }


def create_app() -> Any:
    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed. Please install fastapi and uvicorn.")

    app = FastAPI(title="RAG Monitoring Dashboard")

    # Import chatbot routes
    try:
        from .chatbot import router as chat_router
        app.include_router(chat_router)
        print("✓ Chatbot routes loaded")
    except Exception as e:
        print(f"⚠ Could not load chatbot routes: {e}")

    # Static dashboard files
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'dashboard')
    if os.path.isdir(static_dir):
        app.mount('/static', StaticFiles(directory=static_dir), name='static')

    @app.get('/')
    def index() -> HTMLResponse:
        html_path = os.path.join(static_dir, 'index.html')
        if os.path.isfile(html_path):
            with open(html_path, 'r', encoding='utf-8') as f:
                return HTMLResponse(f.read())
        return HTMLResponse("<h3>Dashboard not found. Visit /api/metrics or /chat</h3>")

    @app.get('/chat')
    def chat_page() -> HTMLResponse:
        html_path = os.path.join(static_dir, 'chat.html')
        if os.path.isfile(html_path):
            with open(html_path, 'r', encoding='utf-8') as f:
                return HTMLResponse(f.read())
        return HTMLResponse("<h3>Chat interface not found</h3>")

    @app.get('/api/metrics')
    def api_metrics() -> Dict[str, Any]:
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        system_path = os.path.join(root, 'metrics', 'system.json')
        bench_path = os.path.join(root, 'metrics', 'benchmark.json')
        system = read_json(system_path)
        bench = read_json(bench_path)
        if not system:
            system = build_system_metrics()
        return {
            'system': system,
            'benchmark': bench,
        }

    return app


app = create_app()

if __name__ == '__main__':
    try:
        import uvicorn
        uvicorn.run(app, host='0.0.0.0', port=8000)
    except Exception:
        print("Please install uvicorn: pip install uvicorn fastapi")