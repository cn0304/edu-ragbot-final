2025/12/01
python script.py input.txt --output Courses.md 

python scripts/ingest.py

============================================================
OPTION 1: RUN WITH DOCKER (RECOMMENDED)
============================================================
Make sure Docker Desktop is installed and running.

Build and start the server:
docker compose up -d --build

Open in browser:
http://127.0.0.1:8000

To stop:
docker compose down

============================================================
OPTION 2: RUN WITHOUT DOCKER (MANUAL)
============================================================
Start the server manually:
uvicorn backend.app.web_dashboard:app --reload --port 8000

Open in browser:
http://127.0.0.1:8000

============================================================
QUERY TESTING (scripts/test.py)
============================================================

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

Scholarship
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

============================================================
CI/CD
============================================================

python scripts/run_all_scrapers.py
python scripts/ingest.py
python scripts/update_metrics.py

============================================================
RERANKER
============================================================
python scripts/test_reranker.py

================================================================================
PER-QUERY BREAKDOWN
Query                                         Time Δ (ms)     Score Δ
-----------------------------------------------------------------------
How do I apply to INTI?                           -113ms      +0.000
What is the programme structure for comput         -53ms      +0.000

============================================================
DOCKER DEPLOYMENT
============================================================

docker build -t edu-rag:latest .
docker compose build