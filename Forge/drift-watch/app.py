from flask import Flask, render_template, jsonify
import subprocess
import sys
from pathlib import Path
import json
import re

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
SCANNER = BASE_DIR / "drift_watch.py"
TARGET = BASE_DIR / "fixtures"
STATE_FILE = BASE_DIR / ".drift_state.json"


def run_drift_watch():
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(SCANNER),
                str(TARGET)
            ],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        output = result.stdout

        if result.returncode not in {0, 2}:
            return {
                "success": False,
                "error": result.stderr or output
            }

        return {
            "success": True,
            "output": output
        }

    except Exception as error:
        return {
            "success": False,
            "error": str(error)
        }


def parse_report(output):
    pattern = re.compile(
        r"^\s*-\s*\*\*(?P<key>.+?)\*\*\s+[—-]\s+"
        r"`(?P<category>[^`]+)`\s+"
        r"\(`(?P<severity>Critical|Warning|Info)`\)\s+[—-]\s+"
        r"`(?P<environment>[^`]+)`\s*$",
        re.MULTILINE,
    )

    findings = []

    for match in pattern.finditer(output):
        section_end = output.find("\n- **", match.end())
        section = output[match.end():]

        if section_end != -1:
            section = output[match.end():section_end]

        reference_match = re.search(
            r"^  - Reference: (.+)$",
            section,
            re.MULTILINE,
        )

        findings.append({
            "key": match.group("key").strip(),
            "severity": match.group("severity"),
            "environment": match.group("environment"),
            "location": reference_match.group(1).strip()
            if reference_match else "Unknown",
            "category": match.group("category"),
        })

    return findings


@app.route("/")
def index():
    result = run_drift_watch()

    if not result["success"]:
        return render_template(
            "index.html",
            findings=[],
            error=result["error"],
            summary={
                "total": 0,
                "critical": 0,
                "warning": 0,
                "info": 0
            }
        )

    findings = parse_report(result["output"])

    summary = {
        "total": len(findings),
        "critical": sum(
            1 for item in findings
            if item["severity"] == "Critical"
        ),
        "warning": sum(
            1 for item in findings
            if item["severity"] == "Warning"
        ),
        "info": sum(
            1 for item in findings
            if item["severity"] == "Info"
        )
    }

    return render_template(
        "index.html",
        findings=findings,
        error=None,
        summary=summary
    )


@app.route("/api/scan")
def api_scan():
    result = run_drift_watch()

    if not result["success"]:
        return jsonify({
            "success": False,
            "error": result["error"]
        }), 500

    findings = parse_report(result["output"])

    summary = {
        "total": len(findings),
        "critical": sum(
            1 for item in findings
            if item["severity"] == "Critical"
        ),
        "warning": sum(
            1 for item in findings
            if item["severity"] == "Warning"
        ),
        "info": sum(
            1 for item in findings
            if item["severity"] == "Info"
        )
    }

    return jsonify({
        "success": True,
        "summary": summary,
        "findings": findings
    })


@app.route("/api/state")
def api_state():
    try:
        if not STATE_FILE.exists():
            return jsonify({
                "version": 1,
                "known_findings": []
            })

        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

        return jsonify(state)

    except Exception as error:
        return jsonify({
            "error": str(error)
        }), 500


if __name__ == "__main__":
    print()
    print("=" * 50)
    print("        DRIFT WATCH - LOCALHOST")
    print("=" * 50)
    print()
    print("Scanner :", SCANNER)
    print("Target  :", TARGET)
    print()
    print("Dashboard:")
    print("http://127.0.0.1:5000")
    print()
    print("Press CTRL+C to stop.")
    print()

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
