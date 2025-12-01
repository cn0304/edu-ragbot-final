import os
import subprocess
import sys
import json
from datetime import datetime


def find_input_path(base_dir: str) -> str | None:
    pdfs = os.path.join(base_dir, "pdfs")
    if os.path.isdir(pdfs):
        return "./pdfs"

    # NOTE: tuple/list (not a bare string) and return a *relative* path string
    for name in ("input.txt", "input.html", "urls.txt", "url.txt", "url.html", "url.txt.html"):
        candidate = os.path.join(base_dir, name)
        if os.path.isfile(candidate):
            return f"./{name}"

    return None


def run_scraper(dir_path: str, dry_run: bool = False) -> tuple[str, int]:
    script_path = os.path.join(dir_path, "script.py")
    if not os.path.isfile(script_path):
        return (f"Skipping {dir_path} (no script.py)", 0)

    input_arg = find_input_path(dir_path)
    if not input_arg:
        return (f"Skipping {dir_path} (no input source found)", 0)

    cmd = [sys.executable, "script.py", input_arg, "--output", "Courses.md"]
    if dry_run:
        return (f"DRY-RUN: cd {dir_path} && {' '.join(cmd)}", 0)

    print(f"Running: cd {dir_path} && {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=dir_path)
    ret = proc.wait()
    if ret == 0:
        return (f"SUCCESS: {dir_path}", 0)
    else:
        return (f"FAILED: {dir_path} (exit {ret})", ret)


def status_from_result(msg: str, code: int) -> str:
    """Map message and return code to a simple status string."""
    if msg.startswith("Skipping"):
        return "skipped"
    if msg.startswith("DRY-RUN"):
        return "dry_run"
    if code == 0:
        return "success"
    return "failed"


def write_scrape_log(results: list[dict]) -> None:
    # Repo root is one level above this file's directory
    root_dir = os.path.dirname(os.path.dirname(__file__))
    logs_dir = os.path.join(root_dir, "logs", "scrape")
    os.makedirs(logs_dir, exist_ok=True)

    now = datetime.now()
    timestamp = now.isoformat(timespec="seconds")
    filename = f"scrape_{now.strftime('%Y%m%d_%H%M%S')}.jsonl"
    log_path = os.path.join(logs_dir, filename)

    total = len(results)
    success_count = sum(
        1 for r in results if status_from_result(r["msg"], r["code"]) == "success"
    )
    skipped_count = sum(
        1 for r in results if status_from_result(r["msg"], r["code"]) == "skipped"
    )
    failure_count = sum(
        1 for r in results if status_from_result(r["msg"], r["code"]) == "failed"
    )

    with open(log_path, "w", encoding="utf-8") as f:
        # Summary line
        summary = {
            "type": "summary",
            "timestamp": timestamp,
            "total_directories": total,
            "success_count": success_count,
            "skipped_count": skipped_count,
            "failure_count": failure_count,
        }
        f.write(json.dumps(summary) + "\n")

        # One detail line per directory
        for r in results:
            detail = {
                "type": "detail",
                "timestamp": timestamp,
                "directory": r["name"],
                "status": status_from_result(r["msg"], r["code"]),
                "return_code": r["code"],
                "message": r["msg"],
            }
            f.write(json.dumps(detail) + "\n")

    print(f"\nLog written to: {log_path}")


def main():
    dry_run = "--dry-run" in sys.argv
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    if not os.path.isdir(data_dir):
        print("Data directory not found.")
        sys.exit(1)

    failures: list[tuple[str, int]] = []
    messages: list[str] = []
    results: list[dict] = []

    for name in sorted(os.listdir(data_dir)):
        base = os.path.join(data_dir, name)
        if not os.path.isdir(base):
            continue
        msg, code = run_scraper(base, dry_run=dry_run)
        print(msg)
        messages.append(msg)
        results.append({"name": name, "msg": msg, "code": code})
        if code != 0:
            failures.append((name, code))

    print("\nSUMMARY:")
    for m in messages:
        print(f" - {m}")

    # Write structured log file for this run
    write_scrape_log(results)

    if failures:
        print("\nFailures:")
        for n, c in failures:
            print(f" - {n} (exit {c})")
        sys.exit(1)
    else:
        print("\nAll scrapers completed successfully.")


if __name__ == "__main__":
    main()
