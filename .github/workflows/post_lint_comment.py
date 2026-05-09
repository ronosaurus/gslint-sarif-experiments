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


def make_url(repo, number, sha, filename, line, diff_lines):
    if line in diff_lines.get(filename, set()):
        file_hash = hashlib.sha256(filename.encode()).hexdigest()
        return f"https://github.com/{repo}/pull/{number}/files#diff-{file_hash}R{line}"
    return f"https://github.com/{repo}/blob/{sha}/{filename}#L{line}"


def build_body(lint_data, repo, number, sha, diff_lines):
    sections = ["## gslint results\n"]

    for file_data in lint_data['files']:
        filename = file_data['file']
        section = [f"**{filename}**"]

        for violations in file_data['violations'].values():
            for v in violations:
                url = make_url(repo, number, sha, filename, v['line'], diff_lines)
                section.append(f"- [Line {v['line']}]({url}): {v['message']}")

        sections.append("\n".join(section))

    return "\n\n".join(sections)


def post_comment(repo, number, body):
    payload = json.dumps({"body": body})
    result = subprocess.run(
        ['gh', 'api', f'repos/{repo}/issues/{number}/comments',
         '--method', 'POST', '--input', '-'],
        input=payload, text=True
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
    body = build_body(lint_data, repo, number, sha, diff_lines)
    post_comment(repo, number, body)
    print(f"Posted comment with {total} violation(s).")


if __name__ == '__main__':
    main()
