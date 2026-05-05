#!/usr/bin/env python3
"""Measure similarity between base query results and synonym results."""

from __future__ import annotations

import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get(
    "BASE_URL",
    "https://search-api-wurthbaer-qa.search-villvay.workers.dev/search",
)
IN_PATH = Path(__file__).resolve().parent / "synonyms.json"
OUT_JSON = Path(__file__).resolve().parent / "synonym_similarity_results.json"
OUT_HTML = Path(__file__).resolve().parent / "synonym_similarity_dashboard.html"

PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "24"))
MAX_PAGES_PER_TERM = int(os.environ.get("MAX_PAGES_PER_TERM", "4"))
REQUEST_TIMEOUT_S = int(os.environ.get("REQUEST_TIMEOUT_S", "40"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
RETRY_BACKOFF_S = float(os.environ.get("RETRY_BACKOFF_S", "1.2"))
REQUEST_DELAY_S = float(os.environ.get("REQUEST_DELAY_S", "0.12"))
TOP_K_COMPARE = int(os.environ.get("TOP_K_COMPARE", "100"))


def normalize_title(text: str) -> str:
    return " ".join(str(text or "").lower().strip().split())


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


def fetch_page(term: str, page: int) -> dict:
    params = {
        "page": str(page),
        "query": term,
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


def collect_titles_for_term(term: str) -> tuple[list[str], dict]:
    all_titles: list[str] = []
    total_results = 0
    total_pages = 0
    pages_fetched = 0

    for page in range(1, MAX_PAGES_PER_TERM + 1):
        payload = fetch_page(term, page)
        summary = payload.get("summary") if isinstance(payload, dict) else {}
        total_results = int((summary or {}).get("total") or total_results or 0)
        total_pages = int((summary or {}).get("totalPages") or total_pages or 0)

        page_titles = extract_titles(payload)
        if not page_titles:
            break
        all_titles.extend(page_titles)
        pages_fetched = page

        if total_pages and page >= total_pages:
            break
        time.sleep(REQUEST_DELAY_S)

    stats = {
        "total_results": total_results,
        "total_pages": total_pages,
        "pages_fetched": pages_fetched,
        "titles_collected": len(all_titles),
    }
    return all_titles, stats


def compare_similarity(base_titles: list[str], synonym_titles: list[str], top_k: int) -> dict:
    base_norm = [normalize_title(t) for t in base_titles[:top_k] if normalize_title(t)]
    syn_norm = [normalize_title(t) for t in synonym_titles[:top_k] if normalize_title(t)]

    base_set = set(base_norm)
    syn_set = set(syn_norm)

    intersection = base_set & syn_set
    union = base_set | syn_set
    base_coverage = (len(intersection) / len(base_set) * 100.0) if base_set else 0.0
    synonym_coverage = (len(intersection) / len(syn_set) * 100.0) if syn_set else 0.0
    jaccard = (len(intersection) / len(union) * 100.0) if union else 0.0

    return {
        "top_k_compared": top_k,
        "base_unique_count": len(base_set),
        "synonym_unique_count": len(syn_set),
        "overlap_count": len(intersection),
        "base_coverage_percent": round(base_coverage, 2),
        "synonym_coverage_percent": round(synonym_coverage, 2),
        "similarity_percent": round(jaccard, 2),
    }


def generate_dashboard_html(payload: dict) -> str:
    rows = payload.get("results") or []
    overall = payload.get("summary") or {}
    compact_rows: list[dict] = []
    for row in rows:
        query = str(row.get("query") or "")
        comparisons = row.get("synonym_comparisons") or []
        compact_comparisons: list[dict] = []
        for comp in comparisons:
            sim = comp.get("similarity") or {}
            compact_comparisons.append(
                {
                    "synonym": str(comp.get("synonym") or ""),
                    "similarity": {
                        "similarity_percent": float(sim.get("similarity_percent") or 0.0),
                        "overlap_count": int(sim.get("overlap_count") or 0),
                        "base_coverage_percent": float(sim.get("base_coverage_percent") or 0.0),
                        "synonym_coverage_percent": float(sim.get("synonym_coverage_percent") or 0.0),
                    },
                }
            )
        compact_rows.append({"query": query, "synonym_comparisons": compact_comparisons})

    rows_json = json.dumps(compact_rows, ensure_ascii=False).replace("</", "<\\/")
    overall_json = json.dumps(overall, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Synonym Similarity Dashboard</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; color: #111; }}
    h1, h2 {{ margin: 0 0 12px; }}
    .meta {{ margin-bottom: 20px; }}
    .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }}
    .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 10px 14px; min-width: 180px; }}
    .tabs {{ margin: 8px 0 12px; }}
    .tabs button {{ margin-right: 8px; padding: 8px 10px; border: 1px solid #bbb; background: #f5f5f5; border-radius: 6px; cursor: pointer; }}
    .tabs button.active {{ background: #111; color: #fff; border-color: #111; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
    th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: left; font-size: 13px; }}
    th {{ background: #f6f6f6; }}
    .bar-wrap {{ width: 180px; background: #eee; border-radius: 6px; overflow: hidden; }}
    .bar {{ height: 14px; background: #4f46e5; }}
    .pass {{ color: #047857; font-weight: 700; }}
    .fail {{ color: #b91c1c; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>Synonym Similarity Dashboard</h1>
  <div class="meta" id="meta"></div>
  <div class="cards" id="cards"></div>
  <h2>Per Synonym Comparison</h2>
  <div class="tabs">
    <button id="btnAll" class="active">All</button>
    <button id="btnPass">Pass (>=50%)</button>
    <button id="btnFail">Fail (&lt;50%)</button>
  </div>
  <div class="tabs">
    <button id="btnDownloadPass">Download Pass Table</button>
    <button id="btnDownloadFail">Download Fail Table</button>
  </div>
  <table id="tbl">
    <thead>
      <tr>
        <th>Query</th>
        <th>Synonym</th>
        <th>Similarity %</th>
        <th>Status</th>
        <th>Visual</th>
        <th>Overlap</th>
        <th>Base Coverage %</th>
        <th>Synonym Coverage %</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>

  <script>
    const rows = {rows_json};
    const summary = {overall_json};
    const PASS_THRESHOLD = 50;

    document.getElementById("meta").innerText =
      "Source: " + (summary.source_file || "") + " | API: " + (summary.api_base || "");

    const cards = document.getElementById("cards");
    let allComparisons = 0;
    let passComparisons = 0;
    rows.forEach(r => {{
      (r.synonym_comparisons || []).forEach(c => {{
        allComparisons += 1;
        const sim = (c.similarity || {{}}).similarity_percent || 0;
        if (sim >= PASS_THRESHOLD) passComparisons += 1;
      }});
    }});
    const failComparisons = allComparisons - passComparisons;
    const cardData = [
      ["Total Queries", summary.total_queries || 0],
      ["Total Synonyms Compared", summary.total_synonym_comparisons || 0],
      ["Pass (>=50%)", passComparisons],
      ["Fail (<50%)", failComparisons],
      ["Average Similarity %", summary.average_similarity_percent || 0],
      ["Median Similarity %", summary.median_similarity_percent || 0],
      ["Min Similarity %", summary.min_similarity_percent || 0],
      ["Max Similarity %", summary.max_similarity_percent || 0]
    ];
    cardData.forEach(([k, v]) => {{
      const d = document.createElement("div");
      d.className = "card";
      d.innerHTML = "<div><strong>" + k + "</strong></div><div style='font-size:22px;margin-top:4px;'>" + v + "</div>";
      cards.appendChild(d);
    }});

    const tbody = document.querySelector("#tbl tbody");
    const btnAll = document.getElementById("btnAll");
    const btnPass = document.getElementById("btnPass");
    const btnFail = document.getElementById("btnFail");
    const btnDownloadPass = document.getElementById("btnDownloadPass");
    const btnDownloadFail = document.getElementById("btnDownloadFail");

    const flatRows = [];
    rows.forEach(r => {{
      (r.synonym_comparisons || []).forEach(c => {{
        const sim = c.similarity || {{}};
        const similarity = sim.similarity_percent || 0;
        flatRows.push({{
          query: r.query || "",
          synonym: c.synonym || "",
          similarity: similarity,
          status: similarity >= PASS_THRESHOLD ? "PASS" : "FAIL",
          overlap: sim.overlap_count || 0,
          baseCoverage: sim.base_coverage_percent || 0,
          synCoverage: sim.synonym_coverage_percent || 0
        }});
      }});
    }});

    function csvEscape(v) {{
      const s = String(v ?? "");
      if (s.includes(',') || s.includes('\\n') || s.includes('"')) {{
        return '"' + s.replace(/"/g, '""') + '"';
      }}
      return s;
    }}

    function downloadTableCsv(filename, list) {{
      const headers = [
        "query",
        "synonym",
        "similarity_percent",
        "status",
        "overlap_count",
        "base_coverage_percent",
        "synonym_coverage_percent"
      ];
      const lines = [headers.join(",")];
      list.forEach(r => {{
        lines.push([
          csvEscape(r.query),
          csvEscape(r.synonym),
          csvEscape(r.similarity),
          csvEscape(r.status),
          csvEscape(r.overlap),
          csvEscape(r.baseCoverage),
          csvEscape(r.synCoverage)
        ].join(","));
      }});
      const blob = new Blob([lines.join("\\n")], {{ type: "text/csv;charset=utf-8;" }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }}

    function activate(btn) {{
      [btnAll, btnPass, btnFail].forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
    }}

    function render(mode) {{
      tbody.innerHTML = "";
      rows.forEach(r => {{
        const all = r.synonym_comparisons || [];
        const filtered = all.filter(c => {{
          const sim = (c.similarity || {{}}).similarity_percent || 0;
          if (mode === "pass") return sim >= PASS_THRESHOLD;
          if (mode === "fail") return sim < PASS_THRESHOLD;
          return true;
        }});

        if (!filtered.length) return;
        filtered.forEach((c, idx) => {{
          const tr = document.createElement("tr");
          const sim = c.similarity || {{}};
          const similarity = sim.similarity_percent || 0;
          const overlap = sim.overlap_count || 0;
          const baseCoverage = sim.base_coverage_percent || 0;
          const synCoverage = sim.synonym_coverage_percent || 0;
          const status = similarity >= PASS_THRESHOLD
            ? "<span class='pass'>PASS</span>"
            : "<span class='fail'>FAIL</span>";

          if (idx === 0) {{
            tr.innerHTML = `
              <td rowspan="${{filtered.length}}">${{r.query}}</td>
              <td>${{c.synonym}}</td>
              <td>${{similarity}}</td>
              <td>${{status}}</td>
              <td><div class="bar-wrap"><div class="bar" style="width:${{Math.max(0, Math.min(100, similarity))}}%"></div></div></td>
              <td>${{overlap}}</td>
              <td>${{baseCoverage}}</td>
              <td>${{synCoverage}}</td>
            `;
          }} else {{
            tr.innerHTML = `
              <td>${{c.synonym}}</td>
              <td>${{similarity}}</td>
              <td>${{status}}</td>
              <td><div class="bar-wrap"><div class="bar" style="width:${{Math.max(0, Math.min(100, similarity))}}%"></div></div></td>
              <td>${{overlap}}</td>
              <td>${{baseCoverage}}</td>
              <td>${{synCoverage}}</td>
            `;
          }}
          tbody.appendChild(tr);
        }});
      }});
    }}

    btnAll.onclick = () => {{ activate(btnAll); render("all"); }};
    btnPass.onclick = () => {{ activate(btnPass); render("pass"); }};
    btnFail.onclick = () => {{ activate(btnFail); render("fail"); }};
    btnDownloadPass.onclick = () => {{
      const passRows = flatRows.filter(r => r.status === "PASS");
      downloadTableCsv("synonym_pass_table.csv", passRows);
    }};
    btnDownloadFail.onclick = () => {{
      const failRows = flatRows.filter(r => r.status === "FAIL");
      downloadTableCsv("synonym_fail_table.csv", failRows);
    }};
    render("all");
  </script>
</body>
</html>
"""


def main() -> None:
    if not IN_PATH.exists():
        raise SystemExit(f"Missing input file: {IN_PATH}")
    raw = json.loads(IN_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("synonyms.json must be a JSON array")

    run_rows = []
    similarity_values: list[float] = []
    comparisons = 0

    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        synonyms = item.get("synonym") or []
        if not query or not isinstance(synonyms, list) or not synonyms:
            continue

        print(f"[{idx}/{len(raw)}] base query: {query}", flush=True)
        base_results, base_stats = collect_titles_for_term(query)

        synonym_rows = []
        for syn in synonyms:
            syn_term = str(syn or "").strip()
            if not syn_term:
                continue
            print(f"   - compare with synonym: {syn_term}", flush=True)
            syn_results, syn_stats = collect_titles_for_term(syn_term)
            sim = compare_similarity(base_results, syn_results, TOP_K_COMPARE)
            similarity_values.append(sim["similarity_percent"])
            comparisons += 1
            synonym_rows.append(
                {
                    "synonym": syn_term,
                    "base_query_results": base_results,
                    "synonym_results": syn_results,
                    "base_stats": base_stats,
                    "synonym_stats": syn_stats,
                    "similarity": sim,
                }
            )

        run_rows.append(
            {
                "query": query,
                "synonym_comparisons": synonym_rows,
            }
        )

    avg_sim = round(sum(similarity_values) / len(similarity_values), 2) if similarity_values else 0.0
    med_sim = round(statistics.median(similarity_values), 2) if similarity_values else 0.0
    min_sim = round(min(similarity_values), 2) if similarity_values else 0.0
    max_sim = round(max(similarity_values), 2) if similarity_values else 0.0

    payload = {
        "summary": {
            "source_file": IN_PATH.name,
            "api_base": BASE_URL,
            "total_queries": len(run_rows),
            "total_synonym_comparisons": comparisons,
            "top_k_compared": TOP_K_COMPARE,
            "average_similarity_percent": avg_sim,
            "median_similarity_percent": med_sim,
            "min_similarity_percent": min_sim,
            "max_similarity_percent": max_sim,
        },
        "results": run_rows,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(generate_dashboard_html(payload), encoding="utf-8")

    print(f"Saved JSON: {OUT_JSON}", flush=True)
    print(f"Saved HTML: {OUT_HTML}", flush=True)


if __name__ == "__main__":
    main()

