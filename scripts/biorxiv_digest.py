#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import re
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
import smtplib
import random


TORONTO_TZ = ZoneInfo("America/Toronto")
BIORXIV_API_BASE = "https://api.biorxiv.org/details"


@dataclass(frozen=True)
class Paper:
    pid: str  # P01, P02, ...
    title: str
    doi: str
    version: str
    date: str
    category: str
    authors: str
    abstract: str

    def biorxiv_url(self) -> str:
        # bioRxiv supports version-specific URLs like:
        # https://www.biorxiv.org/content/10.1101/2019.12.11.123456v2
        if self.doi and self.version:
            return f"https://www.biorxiv.org/content/{self.doi}v{self.version}"
        return f"https://doi.org/{self.doi}" if self.doi else ""


def env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v or ""


def is_8am_toronto(now: datetime) -> bool:
    # Cron can be delayed a bit; accept any time during the 8am hour.
    return True


def fetch_biorxiv_details(
    server: str,
    start_date: str,
    end_date: str,
    category: str = "",
    cursor: int = 0,
    timeout_s: int = 30,
) -> Dict[str, Any]:
    # API format: https://api.biorxiv.org/details/[server]/[interval]/[cursor]/[format]
    # We request JSON explicitly.
    url = f"{BIORXIV_API_BASE}/{server}/{start_date}/{end_date}/{cursor}/json"
    params = {}
    if category.strip():
        params["category"] = category.strip()

    r = requests.get(url, params=params, timeout=timeout_s)
    r.raise_for_status()
    return r.json()


def normalize_collection(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Typical responses wrap records under "collection"
    if isinstance(payload, dict) and "collection" in payload:
        col = payload.get("collection") or []
        if isinstance(col, list):
            return col
    # Fallback: sometimes people paste only the inner list
    if isinstance(payload, list):
        return payload
    return []


def parse_total(payload: Dict[str, Any]) -> Optional[int]:
    # messages[0].total is common in the API
    msgs = payload.get("messages")
    if isinstance(msgs, list) and msgs:
        total = msgs[0].get("total")
        if isinstance(total, (int, float)):
            return int(total)
        if isinstance(total, str) and total.isdigit():
            return int(total)
    return None


def dedupe_keep_latest_version(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Keep the highest version per DOI.
    best: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        doi = str(row.get("doi", "")).strip()
        if not doi:
            continue
        v_raw = str(row.get("version", "")).strip()
        try:
            v = int(v_raw)
        except Exception:
            v = -1
        if doi not in best:
            best[doi] = row
            continue
        try:
            existing_v = int(str(best[doi].get("version", "-1")))
        except Exception:
            existing_v = -1
        if v > existing_v:
            best[doi] = row
    return list(best.values())


def load_recent_papers(server: str, lookback_days: int, category: str) -> List[Paper]:
    now = datetime.now(TORONTO_TZ)
    end_date = now.date().isoformat()
    start_date = (now.date() - timedelta(days=lookback_days)).isoformat()

    all_rows: List[Dict[str, Any]] = []
    cursor = 0
    total: Optional[int] = None

    while True:
        payload = fetch_biorxiv_details(
            server=server,
            start_date=start_date,
            end_date=end_date,
            category=category,
            cursor=cursor,
        )
        rows = normalize_collection(payload)
        if not rows:
            break

        all_rows.extend(rows)

        if total is None:
            total = parse_total(payload)

        cursor += len(rows)
        if len(rows) < 100:
            break
        if total is not None and cursor >= total:
            break

    rows = dedupe_keep_latest_version(all_rows)

    # Sort newest first by date string (YYYY-MM-DD)
    rows.sort(key=lambda r: str(r.get("date", "")), reverse=True)

    papers: List[Paper] = []
    for i, r in enumerate(rows, start=1):
        pid = f"P{i:02d}"
        papers.append(
            Paper(
                pid=pid,
                title=str(r.get("title", "")).strip(),
                doi=str(r.get("doi", "")).strip(),
                version=str(r.get("version", "")).strip(),
                date=str(r.get("date", "")).strip(),
                category=str(r.get("category", "")).strip(),
                authors=str(r.get("authors", "")).strip(),
                abstract=str(r.get("abstract", "")).strip(),
            )
        )
    return papers


def call_gemini(prompt: str, api_key: str) -> str:
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    endpoint_alt = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 16384,
        },
    }

    try:        
        r = requests.post(endpoint, headers=headers, data=json.dumps(body), timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception:
        r = requests.post(endpoint_alt, headers=headers, data=json.dumps(body), timeout=60)
        r.raise_for_status()
        data = r.json()

    # candidates[0].content.parts[*].text
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        return text.strip()
    except Exception:
        return json.dumps(data)


def extract_json(text: str) -> Dict[str, Any]:
    # Accept raw JSON or JSON inside code fences.
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    if cleaned.startswith("{") and cleaned.endswith("}"):
        return json.loads(cleaned)

    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not m:
        raise ValueError("Gemini response did not contain a JSON object.")
    return json.loads(m.group(0))


def build_ai_prompt(interests: str, papers: List[Paper], general_topic: str) -> str:
    def clip(s: str, n: int) -> str:
        s = re.sub(r"\s+", " ", s).strip()
        return s[:n] + ("…" if len(s) > n else "")
    
    lines = []
    lines.append("You are a research assistant helping rank bioRxiv papers for a daily email digest.")
    lines.append("")
    lines.append("USER_INTERESTS:")
    lines.append(interests.strip())
    lines.append("")
    lines.append("TASK:")
    lines.append("- Select the 5 most relevant papers to USER_INTERESTS. Favour papers which target major problems in their respective field, make novel contributions to their field, and have solid methodological rigor relative to the other papers in the list.")
    lines.append("- For each selected paper: produce a three sentence summary that is understandable to a second year undergraduate student in the field.")
    lines.append("- After this, write a short 'general_trends' section (3–6 bullet points) describing what existing paradigms and opinions are changed from the results of these papers. Think beyond the first-order conclusions of the paper, and think about what the consequences of these discoveries are (but do not speculate too much).")
    lines.append(f"- Along with the 'general_trends' section, include a 'general_concept' section explaining {general_topic}. Write 3–4 bullet points only. The first bullet should immediately begin explaining the concept; do not use a title, heading, or naming-only bullet. Bullets should emphasize structure, mechanisms, formal insights, or non-obvious implications, not introductory definitions. Use plain text only (no markdown, no headings, no formatting). Aim for a level of depth appropriate for a well-educated reader who wants a concise but nontrivial conceptual insight rather than a textbook overview.")
    lines.append("- Finally, include a 'specific_concept' section describing any ONE advanced concept (graduate level) from USER_INTERESTS. Choose a concept that is non-introductory, and non-textbook (avoid canonical topics such as allostery, basic Bayesian inference, classic signaling pathways, etc.). The concept does not need to appear in or relate to any of the papers; treat this section as independent enrichment. After the concepts are generated, choose exactly one to write about. Write 3–4 bullet points only. The first bullet should immediately begin explaining the concept; do not use a title or heading bullet. Bullets should focus on mechanism, formal structure, or nuanced implications, not definitions aimed at beginners. Use plain text only (no markdown, no headings, no bold/italics).")
    lines.append("")
    lines.append("OUTPUT: Return ONLY valid JSON with this schema:")
    lines.append('{')
    lines.append('  "top_papers": [')
    lines.append('    {"id": "P01", "summary": "..."},')
    lines.append('    {"id": "P02", "summary": "..."}')
    lines.append('  ],')
    lines.append('  "general_trends": ["...", "..."],')
    lines.append('  "general_concept": ["...", "..."],')
    lines.append('  "specific_concept": ["...", "..."]')
    lines.append('}')
    lines.append("")
    lines.append("PAPERS:")
    for p in papers:
        lines.append(
            f"- [{p.pid}] Title: {clip(p.title, 220)} | Category: {clip(p.category, 60)} | Date: {p.date} | DOI: {p.doi}"
        )
        lines.append(f"  Abstract: {clip(p.abstract, 900)}")
    return "\n".join(lines)


def build_email_html(now_local: datetime, top: List[Dict[str, Any]], id_to_paper: Dict[str, Paper], trends: List[str], general_concept: List[str], specific_concept: List[str], general_topic: str) -> str:
    def esc(s: str) -> str:
        return html.escape(s or "")

    items_html = []
    for idx, entry in enumerate(top, start=1):
        pid = str(entry.get("id", "")).strip()
        p = id_to_paper.get(pid)
        if not p:
            continue
        one_liner = str(entry.get("summary", "")).strip()
        url = p.biorxiv_url() or (f"https://doi.org/{p.doi}" if p.doi else "")

        items_html.append(
            f"""
            <div style="margin: 0 0 18px 0;">
              <h3 style="margin: 0 0 6px 0;">{idx}. <a href="{esc(url)}">{esc(p.title)}</a></h3>
              <div style="font-size: 13px; color: #333;">
                <div><b>Authors:</b> {esc(p.authors)}</div>
                <div><b>Category:</b> {esc(p.category)} &nbsp; <b>Date:</b> {esc(p.date)} &nbsp; <b>DOI:</b> {esc(p.doi)}v{esc(p.version)}</div>
              </div>
              <p style="margin: 8px 0 6px 0;"><b>AI Summary:</b> {esc(one_liner)}</p>
              <p style="margin: 0;"><b>Abstract:</b><br/>{esc(p.abstract)}</p>
            </div>
            """.strip()
        )

    trends_html = "".join(f"<li>{esc(t)}</li>" for t in trends if str(t).strip())
    conceptg_html = "".join(f"<li>{esc(t)}</li>" for t in general_concept if str(t).strip())
    concepts_html = "".join(f"<li>{esc(t)}</li>" for t in specific_concept if str(t).strip())
    date_str = now_local.strftime("%Y-%m-%d")
    time_str = now_local.strftime("%Y-%m-%d %H:%M %Z")

    return f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.35;">
        <h2 style="margin: 0 0 8px 0;">bioRxiv daily digest — {esc(date_str)}</h2>
        <div style="color:#555; font-size: 12px; margin-bottom: 16px;">
          Generated at {esc(time_str)} (America/Toronto)
        </div>

        {''.join(items_html)}

        <hr style="margin: 22px 0;" />
        <h3 style="margin: 0 0 8px 0;">General Trends</h3>
        <ul style="margin: 0; padding-left: 18px;">
          {trends_html}
        </ul>

        <hr style="margin: 22px 0;" />
        <h3 style="margin: 0 0 8px 0;">General Concept: {esc(general_topic)}</h3>
        <ul style="margin: 0; padding-left: 18px;">
          {conceptg_html}
        </ul>

        <hr style="margin: 22px 0;" />
        <h3 style="margin: 0 0 8px 0;">Specific Concept</h3>
        <ul style="margin: 0; padding-left: 18px;">
          {concepts_html}
        </ul>

        <div style="margin-top: 18px; color:#777; font-size: 12px;">
          Automated via GitHub Actions + Gemini + bioRxiv API.
        </div>
      </body>
    </html>
    """.strip()

def _parse_recipients(raw: str) -> List[str]:
    """
    Parse a recipient string that may contain commas or semicolons.
    Returns a list of non-empty, stripped email addresses.
    """
    if not raw:
        return []
    parts = re.split(r"[;,]+", raw)
    return [p.strip() for p in parts if p and p.strip()]

def send_email(subject: str, html_body: str) -> None:
    """
    Send an HTML email. Supports multiple recipients in the env vars:
      EMAIL_TO  -> comma/semicolon separated list (required)
      EMAIL_CC  -> optional comma/semicolon separated list
      EMAIL_BCC -> optional comma/semicolon separated list (kept out of headers)

    Uses SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM (optional).
    """
    smtp_host = env("SMTP_HOST", required=True)
    smtp_port = int(env("SMTP_PORT", "587") or "587")
    smtp_user = env("SMTP_USER", required=True)
    smtp_password = env("SMTP_PASSWORD", required=True)

    # Recipients: allow comma or semicolon separated lists
    email_to_raw = env("EMAIL_TO", required=True)
    email_cc_raw = env("EMAIL_CC", None)        # optional
    email_bcc_raw = env("EMAIL_BCC", None)      # optional

    # EMAIL_FROM falls back to SMTP_USER if not provided
    email_from = env("EMAIL_FROM", smtp_user) or smtp_user

    # Parse recipients
    to_list = _parse_recipients(email_to_raw)
    cc_list = _parse_recipients(email_cc_raw) if email_cc_raw else []
    bcc_list = _parse_recipients(email_bcc_raw) if email_bcc_raw else []

    if not to_list:
        raise ValueError("No recipients in EMAIL_TO. At least one recipient required.")

    # Build message
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    # Do NOT set Bcc header — keep it out of the headers so recipients don't see it

    # Plain text fallback
    msg.set_content(
        "Your email client does not support HTML. Please view this digest in an HTML-capable client."
    )
    msg.add_alternative(html_body, subtype="html")

    # Combine all recipients for SMTP delivery
    all_recipients = to_list + cc_list + bcc_list

    # Send
    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as s:
        s.ehlo()
        s.starttls(context=context)
        s.ehlo()
        s.login(smtp_user, smtp_password)
        # Explicitly pass the recipient list to ensure delivery to CC/BCC too
        s.send_message(msg, from_addr=email_from, to_addrs=all_recipients)


def send_email(subject: str, html_body: str) -> None:
    smtp_host = env("SMTP_HOST", required=True)
    smtp_port = int(env("SMTP_PORT", "587") or "587")
    smtp_user = env("SMTP_USER", required=True)
    smtp_password = env("SMTP_PASSWORD", required=True)
    email_to = env("EMAIL_TO", required=True)
    email_from = env("EMAIL_FROM", smtp_user) or smtp_user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to

    # Plain text fallback
    msg.set_content("Your email client does not support HTML. Please view this digest in an HTML-capable client.")
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=60) as s:
        s.ehlo()
        s.starttls(context=context)
        s.ehlo()
        s.login(smtp_user, smtp_password)
        s.send_message(msg)


def main() -> int:
    now_local = datetime.now(TORONTO_TZ)
    if not is_8am_toronto(now_local):
        print(f"[info] Not 8am in Toronto (now: {now_local.isoformat()}); exiting.")
        return 0

    interests = env("DIGEST_INTERESTS", required=True)
    gemini_key = env("GEMINI_API_KEY", required=True)

    server = env("BIORXIV_SERVER", "biorxiv") or "biorxiv"
    category = env("BIORXIV_CATEGORY", "").strip()
    lookback_days = int(env("LOOKBACK_DAYS", "1") or "1")
    max_for_ai = int(env("MAX_PAPERS_FOR_AI", "60") or "60")

    papers = load_recent_papers(server=server, lookback_days=lookback_days, category=category)
    if not papers:
        print("[info] No papers found for interval; exiting.")
        return 0

    papers_for_ai = papers[: max(10, min(max_for_ai, len(papers)))]
    with open("scripts/topics.json") as f:
        general_topics = json.load(f)

    n = random.randint(0,284)
    today_topic = general_topics[n]
    prompt = build_ai_prompt(interests=interests, papers=papers_for_ai, general_topic=today_topic)

    ai_text = call_gemini(prompt=prompt, api_key=gemini_key)
    ai = extract_json(ai_text)

    top_papers = ai.get("top_papers", [])
    trends = ai.get("general_trends", [])
    general_concept = ai.get("general_concept", [])
    specific_concept = ai.get("specific_concept", [])

    if not isinstance(top_papers, list):
        raise RuntimeError("Gemini returned invalid 'top_papers' format.")
    if not isinstance(trends, list):
        trends = [str(trends)]

    # Only keep the first 5 selections
    top_papers = top_papers[:5]
    id_to_paper = {p.pid: p for p in papers_for_ai}

    subject_date = now_local.strftime("%Y-%m-%d")
    subject = f"bioRxiv digest: ({subject_date})"

    html_body = build_email_html(now_local=now_local, top=top_papers, id_to_paper=id_to_paper, trends=trends, general_concept=general_concept, specific_concept=specific_concept, general_topic=today_topic)
    send_email(subject=subject, html_body=html_body)

    print("[info] Digest sent.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        raise
