2025/12/01
python script.py input.txt --output Courses.md 

python scripts/ingest.py

HOW TO RUN WEB SERVER
uvicorn backend.app.web_dashboard:app --reload --port 8000
open http://127.0.0.1:8000

Course
python scripts/test.py --query "List only INTI all course name"
python scripts/test.py --query "What is the program in PIDC"
python scripts/test.py --query "List only INTI diploma course name"
python scripts/test.py --query "List only UOW master course name"
python scripts/test.py --query "List MCKL all course name"

Fees, Duration, Entry requirement, Intake period, Program structure
python scripts/test.py --query "What is the INTI Bachelor of Information Technology Hons Business Analytics fees?"
python scripts/test.py --query "What is the INTI Bachelor of Information Technology Hons Business Analytics duration?"
python scripts/test.py --query "What is the ATC foundation-in-law entry requirement?" 
python scripts/test.py --query "What is the ATC foundation-in-law intake?"
python scripts/test.py --query "What is the ATC foundation-in-law program structure?"

Scolarship
python scripts/test.py --query "List all INTI scolarship"
python scripts/test.py --query "List all ATC scolarship"
python scripts/test.py --query "List all UOW scolarship"

How to apply
python scripts/test.py --query "How to apply INTI?"
python scripts/test.py --query "How to apply ATC?"
python scripts/test.py --query "How to apply UOW?"

Campus
python scripts/test.py --query "Where is INTI campus located?"
python scripts/test.py --query "How is the campus in INTI?"

Compare
python scripts/test.py --query "How is the INTI bachelor-of-business-hons-with-psychology vs MSU bachelor-marketing-psychology fees?"
python scripts/test.py --query "What is the cheapest fees for Diploma in Business?"


CI/CD 
python scripts/run_all_scrapers.py
python scripts/ingest.py
python scripts/update_metrics.py

RERANKER 
python scripts/test_reranker.py
================================================================================
                              PER-QUERY BREAKDOWN
================================================================================

Query                                         Time Δ (ms)     Score Δ
--------------------------------------------------------------------------------
How do I apply to INTI?                           -113ms      +0.000
What is the programme structure for comput         -53ms      +0.000

Benchmark 
index.html 
accuracy, latency_sec

Docker deployment 
docker build -t edu-rag:latest . (for docker)
docker compose build (for docker compose I recommend this) 
