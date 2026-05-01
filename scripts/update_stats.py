#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "2026"
README = ROOT / "README.md"

REPO = "apache/airflow"
SINCE = "2026-04-22"
CHART_START = "2026-03-01"
PER_PAGE = 100
API = "https://api.github.com/search/issues"
SLEEP = 2.0

TEAMS: list[tuple[str, Path]] = [
    ("Offline Team A", DATA_DIR / "offline_team_A.csv"),
    ("Offline Team B", DATA_DIR / "offline_team_B.csv"),
    ("Online Team", DATA_DIR / "online_team.csv"),
]

MARKER_START = "<!-- STATS:START -->"
MARKER_END = "<!-- STATS:END -->"


def fetch(query: str, token: str | None) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        params = urllib.parse.urlencode({
            "q": query,
            "per_page": PER_PAGE,
            "page": page,
            "sort": "created",
            "order": "desc",
        })
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ossca-stats-bot",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(f"{API}?{params}", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        page_items = data.get("items", [])
        items.extend(page_items)
        if len(page_items) < PER_PAGE:
            break
        page += 1
        time.sleep(SLEEP)
    return items


def extract_username(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def state_label(item: dict) -> str:
    pr = item.get("pull_request") or {}
    if pr.get("merged_at"):
        return "🟣 merged"
    if item.get("state") == "closed":
        return "🔴 closed"
    return "🟢 open"


def search_link(query: str) -> str:
    return f"https://github.com/{REPO}/pulls?q={urllib.parse.quote(query)}"


def render_pr_list(items: list[dict]) -> str:
    if not items:
        return "_없음_"
    lines = []
    for it in items:
        date = (it.get("created_at") or "")[:10]
        title = it["title"].replace("|", "\\|").replace("\n", " ").strip()
        lines.append(
            f"- [#{it['number']} {title}]({it['html_url']}) — "
            f"{state_label(it)} ({date})"
        )
    return "\n".join(lines)


def read_members(csv_path: Path) -> list[dict]:
    with csv_path.open(encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("name")]


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def build_chart(authored_prs: list[dict], reviewed_prs: list[dict]) -> str:
    chart_start = date.fromisoformat(CHART_START)
    days_until_mon = (7 - chart_start.weekday()) % 7
    first_monday = chart_start + timedelta(days=days_until_mon or 7)
    today = datetime.now(timezone.utc).date()
    last_monday = monday_of(today)

    a_created = [p["created_at"][:10] for p in authored_prs]
    r_created = [p["created_at"][:10] for p in reviewed_prs]

    points: list[tuple[date, int, int]] = [(chart_start, 0, 0)]
    d = first_monday
    while d <= last_monday:
        cutoff = (today if d == last_monday else d + timedelta(days=6)).isoformat()
        a = sum(1 for x in a_created if x <= cutoff)
        r = sum(1 for x in r_created if x <= cutoff)
        points.append((d, a, r))
        d += timedelta(days=7)

    labels = [f'"{p.strftime("%m-%d")}"' for p, _, _ in points]
    authored = [a for _, a, _ in points]
    reviewed = [r for _, _, r in points]
    y_max = max(max(authored, default=0), max(reviewed, default=0), 1) + 1

    return "\n".join([
        "```mermaid",
        "xychart-beta",
        f'    title "Apache Airflow PR 누적 추이 ({CHART_START}~)"',
        f'    x-axis [{", ".join(labels)}]',
        f'    y-axis "PR 수" 0 --> {y_max}',
        f'    line [{", ".join(map(str, authored))}]',
        f'    line [{", ".join(map(str, reviewed))}]',
        "```",
        "",
        "> 매주 월요일 시점의 PR 누계 (마지막 점은 오늘 시점). "
        "첫 번째 라인: **작성 PR**, 두 번째 라인: **리뷰 PR**.",
    ])


def build_block(token: str | None) -> tuple[str, list[dict], list[dict]]:
    out: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out.append(f"_마지막 업데이트: {now}_")
    out.append("")

    all_authored: list[dict] = []
    all_reviewed: list[dict] = []

    for team_name, csv_path in TEAMS:
        members = read_members(csv_path)
        rows: list[str] = []
        details: list[str] = []

        for m in members:
            name = m["name"].strip()
            gh_url = m["github_url"].strip()
            user = extract_username(gh_url)

            authored_q = f"repo:{REPO} is:pr author:{user} created:>={SINCE}"
            reviewed_q = f"repo:{REPO} is:pr reviewed-by:{user} created:>={SINCE}"

            try:
                authored = fetch(authored_q, token)
            except Exception as e:
                print(f"[{user}] authored 조회 실패: {e}", file=sys.stderr)
                authored = []
            time.sleep(SLEEP)

            try:
                reviewed = fetch(reviewed_q, token)
            except Exception as e:
                print(f"[{user}] reviewed 조회 실패: {e}", file=sys.stderr)
                reviewed = []
            time.sleep(SLEEP)

            all_authored.extend(authored)
            all_reviewed.extend(reviewed)

            rows.append(
                f"| {name} | [@{user}]({gh_url}) "
                f"| [{len(authored)}]({search_link(authored_q)}) "
                f"| [{len(reviewed)}]({search_link(reviewed_q)}) |"
            )

            details.append(f"#### {name} ([@{user}]({gh_url}))")
            details.append("")
            details.append("**작성한 PR**")
            details.append("")
            details.append(render_pr_list(authored))
            details.append("")
            details.append("**리뷰한 PR**")
            details.append("")
            details.append(render_pr_list(reviewed))
            details.append("")

        out.append(f"### {team_name}")
        out.append("")
        out.append("| 이름 | GitHub | 작성 PR | 리뷰 PR |")
        out.append("|------|--------|---------|---------|")
        out.extend(rows)
        out.append("")
        out.append(f"<details><summary>{team_name} 상세 PR 목록</summary>")
        out.append("")
        out.extend(details)
        out.append("</details>")
        out.append("")

    return "\n".join(out).rstrip() + "\n", all_authored, all_reviewed


def update_readme(block: str) -> None:
    new_section = f"{MARKER_START}\n{block}{MARKER_END}"
    text = README.read_text(encoding="utf-8")

    if MARKER_START in text and MARKER_END in text:
        before, _, rest = text.partition(MARKER_START)
        _, _, after = rest.partition(MARKER_END)
        text = before + new_section + after
    else:
        text = text.rstrip() + "\n\n" + new_section + "\n"

    README.write_text(text, encoding="utf-8")


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("경고: GITHUB_TOKEN 미설정 — rate limit 가능", file=sys.stderr)

    block, all_authored, all_reviewed = build_block(token)
    chart = build_chart(all_authored, all_reviewed)

    update_readme(chart + "\n\n" + block)
    print(
        f"README.md 갱신 완료. (작성 누계 {len(all_authored)}, 리뷰 누계 {len(all_reviewed)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
