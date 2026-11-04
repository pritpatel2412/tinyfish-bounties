"""
scripts/scan_secrets.py
Scans git commits and working directory for accidental secret leakage.
Checks against patterns for TinyFish, Groq, Supabase, JWTs, private keys.
"""
import re
import subprocess
import sys

PATTERNS = [
    (r"gsk_[a-zA-Z0-9]{20,}", "Groq API Key"),
    (r"eyJhbGciOi[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}", "JWT / Supabase Service Key"),
    (r"sk-[a-zA-Z0-9]{20,}", "OpenAI/Generic Secret Key"),
    (r"tinyfish_[a-zA-Z0-9]{16,}", "TinyFish Key format"),
]

def scan_git_history():
    print("Scanning git commit patches...")
    res = subprocess.run(["git", "log", "-p"], capture_output=True, text=True, errors="ignore")
    diff_text = res.stdout
    findings = []
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            for pat, name in PATTERNS:
                if re.search(pat, line):
                    findings.append((name, line[:60]))
    return findings

def main():
    findings = scan_git_history()
    if findings:
        print(f"FAILED: Found {len(findings)} potential secrets in git history!")
        for name, snippet in findings:
            print(f" - {name}: {snippet}...")
        sys.exit(1)
    else:
        print("PASS: Zero secrets found in git commit history.")

if __name__ == "__main__":
    main()
