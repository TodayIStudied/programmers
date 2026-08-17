#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib import parse, request
from urllib.error import HTTPError, URLError


ZERO_SHA = "0" * 40
MAX_PREVIEW_CHARS = 3000
IGNORED_ROOT_MARKDOWN = {"README.md", "Template.md"}


def run_git(args):
    result = subprocess.run(
        ["git", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def is_zero_sha(value):
    return not value or value == ZERO_SHA


def is_markdown_submission(path):
    if not path.endswith(".md"):
        return False
    if path in IGNORED_ROOT_MARKDOWN:
        return False
    if path.startswith("."):
        return False
    return True


def added_markdown_files(before_sha, after_sha):
    if is_zero_sha(after_sha):
        print("Branch deletion push detected; skipping Slack notification.")
        return []

    if is_zero_sha(before_sha):
        output = run_git(["ls-tree", "-r", "--name-only", after_sha])
        return sorted(
            path for path in output.splitlines() if is_markdown_submission(path)
        )

    output = run_git(["diff", "--name-status", "--diff-filter=A", before_sha, after_sha])
    added = []
    for line in output.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, path = parts
        if status == "A" and is_markdown_submission(path):
            added.append(path)
    return sorted(added)


def file_preview(path):
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > MAX_PREVIEW_CHARS
    preview = content[:MAX_PREVIEW_CHARS].replace("```", "'''")
    if truncated:
        preview += "\n\n... truncated. Open the GitHub link for the full file."
    return preview


def github_blob_url(server_url, repository, sha, path):
    quoted_path = parse.quote(path, safe="/")
    return f"{server_url}/{repository}/blob/{sha}/{quoted_path}"


def github_commit_url(server_url, repository, sha):
    return f"{server_url}/{repository}/commit/{sha}"


def post_slack(webhook_url, text):
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


def build_message(path, preview, env):
    server_url = env.get("SERVER_URL", "https://github.com")
    repository = env["REPOSITORY"]
    after_sha = env["AFTER_SHA"]
    branch_name = env["BRANCH_NAME"]
    pusher_name = env["PUSHER_NAME"]
    file_url = github_blob_url(server_url, repository, after_sha, path)
    commit_url = github_commit_url(server_url, repository, after_sha)

    return (
        "*새 Markdown 파일이 추가되었습니다.*\n"
        f"- Branch: `{branch_name}`\n"
        f"- Pusher: `{pusher_name}`\n"
        f"- Commit: <{commit_url}|{after_sha[:7]}>\n"
        f"- File: <{file_url}|{path}>\n\n"
        f"```{preview}```"
    )


def main():
    env = os.environ
    before_sha = env.get("BEFORE_SHA", "")
    after_sha = env.get("AFTER_SHA", "")

    paths = added_markdown_files(before_sha, after_sha)
    if not paths:
        print("No newly added Markdown files found; skipping Slack notification.")
        return 0

    webhook_url = env.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("SLACK_WEBHOOK_URL is required when newly added Markdown files exist.", file=sys.stderr)
        return 1

    for path in paths:
        preview = file_preview(path)
        post_slack(webhook_url, build_message(path, preview, env))
        print(f"Sent Slack notification for {path}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
