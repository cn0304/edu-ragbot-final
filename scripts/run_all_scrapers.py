import os
import subprocess
import sys


def find_input_path(base_dir: str) -> str | None:
    """Determine the input path for a data script.
    Priority:
    1) ./pdfs (Peninsula College style)
    2) input.txt
    """
    pdfs = os.path.join(base_dir, "pdfs")
    if os.path.isdir(pdfs):
        return "./pdfs"
    for name in ("input.txt"):
        candidate = os.path.join(base_dir, name)
        if os.path.isfile(candidate):
            return name
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


def main():
    dry_run = "--dry-run" in sys.argv
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    if not os.path.isdir(data_dir):
        print("Data directory not found.")
        sys.exit(1)

    failures = []
    messages = []
    for name in sorted(os.listdir(data_dir)):
        base = os.path.join(data_dir, name)
        if not os.path.isdir(base):
            continue
        msg, code = run_scraper(base, dry_run=dry_run)
        print(msg)
        messages.append(msg)
        if code != 0:
            failures.append((name, code))

    print("\nSUMMARY:")
    for m in messages:
        print(f" - {m}")
    if failures:
        print("\nFailures:")
        for n, c in failures:
            print(f" - {n} (exit {c})")
        sys.exit(1)
    else:
        print("\nAll scrapers completed successfully.")


if __name__ == "__main__":
    main()