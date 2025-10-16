2025 10 12
python script.py input.txt --output Courses.md 

python scripts/ingest.py
python scripts/test.py --query "List all ATC scolarship"

Course
so far check course ok
python scripts/test.py --query "List only INTI all course name"
python scripts/test.py --query "List only INTI foundation course name"
python scripts/test.py --query "List only INTI diploma course name"
python scripts/test.py --query "List only INTI master course name"
python scripts/test.py --query "List only MCKL all course name"

Fees, Duration, Entry requirement, Intake period, Program structure
python scripts/test.py --query "What is the INTI Bachelor of Information Technology Hons Business Analytics fees?"
python scripts/test.py --query "What is the INTI Bachelor of Information Technology Hons Business Analytics duration?"
python scripts/test.py --query "What is the ATC foundation-in-law entry requirement?" 
python scripts/test.py --query "What is the ATC foundation-in-law intake period?"
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
python scripts/test.py --query "How is the INTI bachelor-of-business-hons-with-psychology vs MSU bachelor-marketing-psychology fees ?"


UNIVERSITY 
1. ATC
2. INTI
3. MSU
4. MCKL
5. PIDC
6. UOW
9. SENTRAL
10. THE ONE ACADEMY
11. UNITAR
13. PSDC
14. ROYAL
7. PENISULA -- PDF
8. tarc -- IMAGE FEES
12. VERITAS -- IMAGE FEES

HOW TO RUN WEB SERVER 
python -m backend.app.web_dashboard
chat.html

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
