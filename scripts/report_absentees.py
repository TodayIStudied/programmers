#!/usr/bin/env python3
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from urllib import parse, request
from urllib.error import HTTPError, URLError


API_ROOT = "https://api.github.com"
KST = timezone(timedelta(hours=8))
MAIN_BRANCH = "main"


def require_env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value


def parse_target_date():
    value = os.environ.get("TARGET_DATE_KST", "").strip()
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise RuntimeError("TARGET_DATE_KST must use YYYY-MM-DD format.") from exc
    return (datetime.now(KST).date() - timedelta(days=1))


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def github_get(path, params=None):
    token = require_env("GITHUB_TOKEN")
    url = f"{API_ROOT}{path}"
    if params:
        url = f"{url}?{parse.urlencode(params)}"

    req = request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "programmers-attendance-bot",
        },
    )

    try:
        with request.urlopen(req, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API failed: HTTP {exc.code} {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"GitHub API failed: {exc.reason}") from exc


def paged_github_get(path, result_key, params=None):
    page = 1
    items = []
    while True:
        page_params = dict(params or {})
        page_params.update({"per_page": 100, "page": page})
        data = github_get(path, page_params)
        if isinstance(data, list):
            page_items = data
        else:
            page_items = data.get(result_key, [])
        items.extend(page_items)
        if len(page_items) < 100:
            break
        page += 1
    return items


def repository_parts():
    repository = require_env("REPOSITORY")
    try:
        owner, repo = repository.split("/", 1)
    except ValueError as exc:
        raise RuntimeError("REPOSITORY must be in owner/repo format.") from exc
    return parse.quote(owner, safe=""), parse.quote(repo, safe="")


def list_member_branches(owner, repo):
    branches = paged_github_get(f"/repos/{owner}/{repo}/branches", result_key=None)
    return sorted(
        branch["name"]
        for branch in branches
        if branch.get("name") and branch["name"] != MAIN_BRANCH
    )


def list_push_workflow_branches(owner, repo, target_date):
    workflow_file = parse.quote(require_env("WORKFLOW_FILE_NAME"), safe="")
    start_kst = datetime.combine(target_date, time.min, tzinfo=KST)
    end_kst = datetime.combine(target_date, time(23, 59, 59), tzinfo=KST)
    created_range = f"{iso_z(start_kst)}..{iso_z(end_kst)}"

    runs = paged_github_get(
        f"/repos/{owner}/{repo}/actions/workflows/{workflow_file}/runs",
        "workflow_runs",
        {"event": "push", "created": created_range},
    )
    return sorted(
        {
            run["head_branch"]
            for run in runs
            if run.get("head_branch") and run["head_branch"] != MAIN_BRANCH
        }
    )


def post_slack(text):
    webhook_url = require_env("SLACK_WEBHOOK_URL")
    payload = json.dumps({"text": text, "unfurl_links": False, "unfurl_media": False}).encode(
        "utf-8"
    )
    req = request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Slack webhook failed: HTTP {exc.code} {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Slack webhook failed: {exc.reason}") from exc

    if body and body.strip().lower() != "ok":
        print(f"Slack webhook response: {body}")


def attendance_message(target_date, member_branches, pushed_branches, absentees):
    if not member_branches:
        return f"*{target_date.isoformat()} 출결 체크*\n대상 브랜치가 없습니다."

    if not absentees:
        return (
            f"*{target_date.isoformat()} 출결 체크*\n"
            f"전원 push 완료입니다. ({len(pushed_branches)}/{len(member_branches)})"
        )

    absentee_lines = "\n".join(f"- `{branch}`" for branch in absentees)
    return (
        f"*{target_date.isoformat()} 출결 체크*\n"
        f"전날 push 기록이 없는 브랜치입니다. ({len(absentees)}명)\n\n"
        f"{absentee_lines}"
    )


def main():
    try:
        owner, repo = repository_parts()
        target_date = parse_target_date()
        member_branches = list_member_branches(owner, repo)
        pushed_branches = list_push_workflow_branches(owner, repo, target_date)
        absentees = sorted(set(member_branches) - set(pushed_branches))
        message = attendance_message(target_date, member_branches, pushed_branches, absentees)
        post_slack(message)
        print(
            f"Reported attendance for {target_date.isoformat()}: "
            f"{len(absentees)} absent, {len(pushed_branches)} pushed, "
            f"{len(member_branches)} member branches."
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
