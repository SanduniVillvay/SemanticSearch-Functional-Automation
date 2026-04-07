#!/usr/bin/env python3
"""Run benchmark_final.json queries against search API and save actual results."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from html import escape
from pathlib import Path

BASE_URL = os.environ.get(
    "BASE_URL",
    "https://search-api-wurthbaer-qa.search-villvay.workers.dev/search",
)
IN_PATH = Path(__file__).resolve().parent / "benchmark_final.json"
OUT_PATH = Path(__file__).resolve().parent / "benchmark_final_actual_results.json"
RESULTS_DIR = Path(__file__).resolve().parent / "results"
GRAPH_REPORT_PATH = RESULTS_DIR / "precision_recall_issue_report.html"

PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "24"))
MAX_PAGES_PER_QUERY = int(os.environ.get("MAX_PAGES_PER_QUERY", "200"))
REQUEST_TIMEOUT_S = int(os.environ.get("REQUEST_TIMEOUT_S", "60"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
RETRY_BACKOFF_S = float(os.environ.get("RETRY_BACKOFF_S", "1.5"))
PAGE_DELAY_S = float(os.environ.get("PAGE_DELAY_S", "0.12"))


def percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def extract_titles(payload: dict) -> list[str]:
    results = payload.get("results") if isinstance(payload, dict) else None
    products = results.get("products") if isinstance(results, dict) else None
    if not isinstance(products, list):
        return []

    out: list[str] = []
    for p in products:
        if isinstance(p, str):
            t = p.strip()
        elif isinstance(p, dict):
            t = (
                p.get("productTitle")
                or p.get("primaryProductTitle")
                or p.get("title")
                or p.get("name")
                or ""
            )
            t = str(t).strip()
        else:
            t = ""
        if t:
            out.append(t)
    return out


def fetch_page(query: str, page: int) -> dict:
    params = {
        "page": str(page),
        "query": query,
        "isFilterByBrand": "false",
        "pageSize": str(PAGE_SIZE),
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("accept", "application/json")
    req.add_header(
        "user-agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    )

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_S * attempt)
            else:
                raise last_err
    raise RuntimeError("unreachable")


def run_one_query(query: str) -> tuple[list[str], dict]:
    all_titles: list[str] = []
    total_pages = 0
    total_results = 0

    for page in range(1, MAX_PAGES_PER_QUERY + 1):
        payload = fetch_page(query, page)
        summary = payload.get("summary") if isinstance(payload, dict) else {}
        total_results = int((summary or {}).get("total") or total_results or 0)
        total_pages = int((summary or {}).get("totalPages") or total_pages or 0)

        page_titles = extract_titles(payload)
        if not page_titles:
            break
        all_titles.extend(page_titles)

        if total_pages and page >= total_pages:
            break
        time.sleep(PAGE_DELAY_S)

    return all_titles, {
        "api_total_results": total_results,
        "api_total_pages": total_pages,
        "titles_returned": len(all_titles),
    }


def compute_benchmark_intersection(
    benchmark_results: list[str], actual_results: list[str]
) -> tuple[int, list[str]]:
    """Return common benchmark titles found in actual results."""
    actual_norm = {
        str(title).strip().lower()
        for title in actual_results
        if str(title).strip()
    }

    matches: list[str] = []
    seen: set[str] = set()
    for title in benchmark_results:
        cleaned = str(title).strip()
        norm = cleaned.lower()
        if cleaned and norm in actual_norm and norm not in seen:
            matches.append(cleaned)
            seen.add(norm)

    return len(matches), matches


def compute_benchmark_missing(
    benchmark_results: list[str], actual_results: list[str]
) -> tuple[int, list[str]]:
    """Return benchmark titles that were not found in actual results."""
    actual_norm = {
        str(title).strip().lower()
        for title in actual_results
        if str(title).strip()
    }

    missing: list[str] = []
    seen: set[str] = set()
    for title in benchmark_results:
        cleaned = str(title).strip()
        norm = cleaned.lower()
        if cleaned and norm not in actual_norm and norm not in seen:
            missing.append(cleaned)
            seen.add(norm)

    return len(missing), missing


def write_graphical_report(rows: list[dict]) -> None:
    """Write a simple graphical HTML report for precision and recall issues."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    body_rows: list[str] = []
    for row in rows:
        query = escape(str(row.get("query") or ""))
        precision = float(row.get("precision_percentage") or 0.0)
        recall_issue = float(row.get("recall_issue_percentage") or 0.0)
        body_rows.append(
            "<tr>"
            f"<td>{query}</td>"
            f"<td>{precision:.2f}%</td>"
            "<td><div class='bar-wrap'><div class='bar precision' "
            f"style='width:{precision:.2f}%'></div></div></td>"
            f"<td>{recall_issue:.2f}%</td>"
            "<td><div class='bar-wrap'><div class='bar recall-issue' "
            f"style='width:{recall_issue:.2f}%'></div></div></td>"
            "</tr>"
        )

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Precision vs Recall Issue by Query</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      margin: 20px;
      background: #f8fafc;
      color: #0f172a;
    }}
    h1 {{ margin: 0 0 14px 0; font-size: 24px; }}
    p {{ margin: 0 0 16px 0; color: #334155; }}
    .card {{
      background: #fff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 12px;
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 860px;
    }}
    th, td {{
      border-bottom: 1px solid #e2e8f0;
      text-align: left;
      padding: 10px 8px;
      font-size: 13px;
      vertical-align: middle;
    }}
    th {{ background: #f1f5f9; position: sticky; top: 0; }}
    .bar-wrap {{
      width: 220px;
      height: 10px;
      border-radius: 999px;
      background: #e2e8f0;
      overflow: hidden;
    }}
    .bar {{
      height: 100%;
      border-radius: 999px;
    }}
    .precision {{ background: #0ea5e9; }}
    .recall-issue {{ background: #f97316; }}
  </style>
</head>
<body>
  <h1>Query-wise Precision and Recall Issue</h1>
  <p>Generated automatically from benchmark run output.</p>
  <div class="card">
    <table>
      <thead>
        <tr>
          <th>Query Name</th>
          <th>Precision %</th>
          <th>Precision Graph</th>
          <th>Recall Issue %</th>
          <th>Recall Issue Graph</th>
        </tr>
      </thead>
      <tbody>
        {"".join(body_rows)}
      </tbody>
    </table>
  </div>
</body>
</html>
"""
    GRAPH_REPORT_PATH.write_text(html, encoding="utf-8")


def main() -> None:
    if not IN_PATH.exists():
        raise SystemExit(f"Missing input file: {IN_PATH}")

    data = json.loads(IN_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("benchmark_final.json must be a JSON array")

    out_rows = []
    total = len(data)
    for idx, row in enumerate(data, start=1):
        query_id = row.get("query_id")
        query = str(row.get("query") or "").strip()
        benchmark_results = row.get("benchmark_results") or []
        number_of_results = int(row.get("number_of_results") or 0)
        if not query:
            continue

        print(f"[{idx}/{total}] query_id={query_id} | {query}", flush=True)

        try:
            actual_results, stats = run_one_query(query)
            match_count, match_results = compute_benchmark_intersection(
                benchmark_results, actual_results
            )
            missing_count, missing_results = compute_benchmark_missing(
                benchmark_results, actual_results
            )
            precision_pct = percentage(match_count, len(actual_results))
            recall_pct = percentage(match_count, len(benchmark_results))
            recall_issue_pct = percentage(missing_count, len(benchmark_results))
            out_rows.append(
                {
                    "query_id": query_id,
                    "query": query,
                    "number_of_results": number_of_results,
                    "benchmark_results": benchmark_results,
                    "actual_results": actual_results,
                    "benchmark_matching_count(Precision)": match_count,
                    "benchmark_matching_results(Precision)": match_results,
                    "benchmark_missing_count(Recall_Issue)": missing_count,
                    "benchmark_missing_results(Recall_issue)": missing_results,
                    "precision_percentage": precision_pct,
                    "recall_percentage": recall_pct,
                    "recall_issue_percentage": recall_issue_pct,
                    "api_stats": stats,
                    "error": "",
                }
            )
        except Exception as e:  # noqa: BLE001
            out_rows.append(
                {
                    "query_id": query_id,
                    "query": query,
                    "number_of_results": number_of_results,
                    "benchmark_results": benchmark_results,
                    "actual_results": [],
                    "benchmark_matching_count(Precision)": 0,
                    "benchmark_matching_results(Precision)": [],
                    "benchmark_missing_count(Recall_Issue)": 0,
                    "benchmark_missing_results(Recall_issue)": [],
                    "precision_percentage": 0.0,
                    "recall_percentage": 0.0,
                    "recall_issue_percentage": 0.0,
                    "api_stats": {},
                    "error": str(e),
                }
            )

    payload = {
        "source_file": IN_PATH.name,
        "api_base": BASE_URL,
        "count": len(out_rows),
        "results": out_rows,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {OUT_PATH}", flush=True)
    write_graphical_report(out_rows)
    print(f"Saved graphical report: {GRAPH_REPORT_PATH}", flush=True)


if __name__ == "__main__":
    main()

