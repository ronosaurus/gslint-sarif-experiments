import hashlib
import json
import os
import re
import subprocess
import sys


def get_diff_lines(repo, number):
    """Returns {filename: set_of_line_numbers} for lines present in the PR diff."""
    result = subprocess.run(
        ['gh', 'api', f'repos/{repo}/pulls/{number}/files'],
        capture_output=True, text=True
    )
    result.check_returncode()

    diff_lines = {}
    for f in json.loads(result.stdout):
        filename = f['filename']
        patch = f.get('patch', '')
        in_diff = set()
        current_line = 0

        for line in patch.splitlines():
            hunk = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if hunk:
                current_line = int(hunk.group(1))
                continue
            if line.startswith('-'):
                continue  # deleted line, no new-file line number
            if line.startswith('+'):
                in_diff.add(current_line)
            current_line += 1

        diff_lines[filename] = in_diff

    return diff_lines


def post_review(repo, number, sha, inline_comments):
    """Post all in-diff violations as a single batched PR review."""
    payload = json.dumps({
        "commit_id": sha,
        "event": "COMMENT",
        "comments": [
            {"path": c["filename"], "line": c["line"], "body": c["message"]}
            for c in inline_comments
        ]
    })
    result = subprocess.run(
        ['gh', 'api', f'repos/{repo}/pulls/{number}/reviews',
         '--method', 'POST', '--input', '-'],
        input=payload, text=True, stdout=subprocess.DEVNULL
    )
    result.check_returncode()


def build_body(all_violations, repo, number, sha, diff_lines):
    """Build the summary comment body covering all violations.

    In-diff lines link into the PR diff; out-of-diff lines link to the blob.
    """
    sections = ["## gslint results\n"]
    by_file = {}
    for v in all_violations:
        by_file.setdefault(v["filename"], []).append(v)

    for filename, violations in by_file.items():
        section = [f"**{filename}**"]
        for v in violations:
            if v["line"] in diff_lines.get(filename, set()):
                file_hash = hashlib.sha256(filename.encode()).hexdigest()
                url = f"https://github.com/{repo}/pull/{number}/files#diff-{file_hash}R{v['line']}"
            else:
                url = f"https://github.com/{repo}/blob/{sha}/{filename}#L{v['line']}"
            section.append(f"- [Line {v['line']}]({url}): {v['message']}")
        sections.append("\n".join(section))

    return "\n\n".join(sections)


def post_comment(repo, number, body):
    payload = json.dumps({"body": body})
    result = subprocess.run(
        ['gh', 'api', f'repos/{repo}/issues/{number}/comments',
         '--method', 'POST', '--input', '-'],
        input=payload, text=True, stdout=subprocess.DEVNULL
    )
    result.check_returncode()


def main():
    repo, sha, number = sys.argv[1], sys.argv[2], sys.argv[3]

    with open('gslint.json') as f:
        lint_data = json.load(f)

    total = sum(
        len(v)
        for file_data in lint_data['files']
        for v in file_data['violations'].values()
    )
    if total == 0:
        print("No violations found, skipping comment.")
        return

    diff_lines = get_diff_lines(repo, number)

    inline_comments = []
    out_of_diff = []

    for file_data in lint_data['files']:
        filename = file_data['file']
        for violations in file_data['violations'].values():
            for v in violations:
                entry = {"filename": filename, "line": v['line'], "message": v['message']}
                if v['line'] in diff_lines.get(filename, set()):
                    print(f"  Line {v['line']} ({filename}): in diff -> inline comment")
                    inline_comments.append(entry)
                else:
                    print(f"  Line {v['line']} ({filename}): not in diff -> summary only")
                    out_of_diff.append(entry)

    if inline_comments:
        post_review(repo, number, sha, inline_comments)
        print(f"Posted {len(inline_comments)} inline comment(s).")

    body = build_body(inline_comments + out_of_diff, repo, number, sha, diff_lines)
    post_comment(repo, number, body)
    print(f"Posted summary with {total} violation(s).")


if __name__ == '__main__':
    main()
