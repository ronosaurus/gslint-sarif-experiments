import hashlib
import json
import os
import re
import subprocess
import sys

# Characters that have special meaning in GitHub Markdown.
_MD_SPECIAL = re.compile(r'([\\`*_{}[\]()#+\-.!|<>])')

# GitHub rejects a review with more than 50 inline comments (HTTP 422).
# Stay safely below that limit; violations above the cap fall back to the summary comment.
INLINE_CAP = 40


def escape_md(text):
    """Escape Markdown metacharacters so violation messages render as plain text."""
    return _MD_SPECIAL.sub(r'\\\1', text)


def validate_args():
    if len(sys.argv) != 4:
        sys.exit("Usage: post_lint_comment.py <repo> <sha> <pr_number>")
    return sys.argv[1], sys.argv[2], sys.argv[3]


def validate_lint_data(data):
    if not isinstance(data, dict) or 'files' not in data:
        sys.exit("gslint.json must be a JSON object with a top-level 'files' key.")
    if not isinstance(data['files'], list):
        sys.exit("gslint.json 'files' must be a list.")
    for i, fd in enumerate(data['files']):
        if 'file' not in fd or 'violations' not in fd:
            sys.exit(f"gslint.json files[{i}] must have 'file' and 'violations' keys.")
        if not isinstance(fd['violations'], dict):
            sys.exit(f"gslint.json files[{i}]['violations'] must be a dict.")
        for rule, vlist in fd['violations'].items():
            if not isinstance(vlist, list):
                sys.exit(f"gslint.json files[{i}]['violations']['{rule}'] must be a list.")
            for j, v in enumerate(vlist):
                if not isinstance(v, dict) or 'line' not in v or 'message' not in v:
                    sys.exit(f"gslint.json files[{i}]['violations']['{rule}'][{j}] must have 'line' and 'message'.")
                if not isinstance(v['line'], int):
                    sys.exit(f"gslint.json files[{i}]['violations']['{rule}'][{j}]['line'] must be an int.")


def get_diff_lines(repo, number):
    """Returns {filename: set_of_line_numbers} for lines present in the PR diff.

    --paginate fetches every page; --slurp wraps them into one outer JSON array
    so json.loads() gets a single list-of-lists rather than concatenated JSON blobs.
    """
    result = subprocess.run(
        ['gh', 'api', '--paginate', '--slurp', f'repos/{repo}/pulls/{number}/files'],
        capture_output=True, text=True
    )
    result.check_returncode()

    # --slurp produces [[page1_item, ...], [page2_item, ...], ...]; flatten one level.
    pages = json.loads(result.stdout)
    diff_lines = {}
    for f in (item for page in pages for item in page):
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


def build_body(out_of_diff_violations, repo, number, sha, diff_lines, inline_count=0, capped=False):
    """Build a summary comment covering out-of-diff violations.

    inline_count > 0: prepend a note that some violations were also posted inline.
    capped=True: inline review was skipped entirely; all violations appear here.
    """
    sections = ["## gslint results\n"]

    if capped:
        sections[0] += (
            f"_Inline review comments skipped: {inline_count} in-diff violation(s) exceed "
            f"the {INLINE_CAP}-comment limit. All findings are listed below._\n"
        )
    elif inline_count > 0:
        sections[0] += f"_{inline_count} violation(s) also posted as inline review comment(s)._\n"

    by_file = {}
    for v in out_of_diff_violations:
        by_file.setdefault(v["filename"], []).append(v)

    for filename, violations in by_file.items():
        section = [f"**{filename}**"]
        for v in violations:
            # These violations are by definition NOT in the diff, so always link to the blob.
            url = f"https://github.com/{repo}/blob/{sha}/{filename}#L{v['line']}"
            section.append(f"- [Line {v['line']}]({url}): {escape_md(v['message'])}")
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
    repo, sha, number = validate_args()

    with open('gslint.json') as f:
        lint_data = json.load(f)
    validate_lint_data(lint_data)

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

    # If in-diff violations would exceed GitHub's review comment limit, skip the inline
    # review entirely and fold everything into the summary comment instead.
    capped = len(inline_comments) > INLINE_CAP
    original_inline_count = len(inline_comments)
    if capped:
        print(f"  {original_inline_count} inline comment(s) exceed cap of {INLINE_CAP}; falling back to summary.")
        out_of_diff = inline_comments + out_of_diff
        inline_comments = []

    if inline_comments:
        post_review(repo, number, sha, inline_comments)
        print(f"Posted {len(inline_comments)} inline comment(s).")

    # Post a summary when there are out-of-diff violations or when the inline cap was hit.
    if out_of_diff:
        body = build_body(
            out_of_diff, repo, number, sha, diff_lines,
            inline_count=original_inline_count if capped else len(inline_comments),
            capped=capped,
        )
        post_comment(repo, number, body)
        print(f"Posted summary with {len(out_of_diff)} violation(s).")
    else:
        print("All violations posted inline; skipping summary comment.")


if __name__ == '__main__':
    main()
