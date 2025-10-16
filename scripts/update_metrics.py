import json
import os
import time
import sys
from typing import Dict, Any

# Ensure repo root is on sys.path for importing backend.*
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.app.rag_engine import SmartRAGEngine


def gather_system_metrics() -> Dict[str, Any]:
    rag = SmartRAGEngine()
    doc_count = rag.collection.count()
    root = os.path.dirname(os.path.dirname(__file__))
    data_dir = os.path.join(root, 'data')
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
        # Only Peninsula College needs pdfs check
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


def write_json(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def main() -> None:
    root = os.path.dirname(os.path.dirname(__file__))
    system_path = os.path.join(root, 'metrics', 'system.json')
    data = gather_system_metrics()
    write_json(system_path, data)
    print(f"Wrote system metrics to {system_path}")


if __name__ == '__main__':
    main()