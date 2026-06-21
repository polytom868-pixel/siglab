"""Fix E701 (colon) and E702 (semicolon) multiple-statement errors.

Usage: python _fix_multi_stmt.py <filenames...>
Or:    ruff check tests/ --output-format=concise 2>&1 | grep -E "E701|E702" | python _fix_multi_stmt.py
"""
import re
import sys


def fix_e701_e702(text: str) -> str:
    """Fix multiple-statement-on-one-line issues in Python source text."""
    lines = text.split("\n")
    result = list(lines)

    for i in range(len(lines)):
        line = result[i]
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = line[: len(line) - len(stripped)]

        # Skip docstrings (triple-quoted strings)
        if stripped.startswith('"""') or stripped.startswith("'''"):
            continue

        # Check if we're inside a string (simple heuristic)
        in_string = False
        string_char = None
        escape = False

        # Find colons in compound statement headers
        # Pattern: keyword condition : body
        for kw in ("if ", "elif ", "else:", "for ", "while ", "with ", "try:", "except ", "finally:"):
            if not stripped.startswith(kw):
                continue

            # Find colon positions not in strings
            colons = []
            in_s = False
            sc = None
            for j, ch in enumerate(stripped):
                if not in_s:
                    if ch in ("'", '"'):
                        in_s = True
                        sc = ch
                    elif ch == ":":
                        colons.append(j)
                    elif ch == "#":
                        break
                else:
                    if ch == "\\":
                        escape = not escape
                    elif ch == sc and not escape:
                        in_s = False
                        sc = None

            if not colons:
                break  # no colon found (shouldn't happen for these keywords)

            colon_pos = colons[0]
            after_colon = stripped[colon_pos+1:].lstrip()

            # Check if there's actually a body on the same line (not just whitespace/newline)
            if not after_colon:
                break

            # Check it's really a single body (not a compound body with nested if/for etc)
            # Simple heuristic: if after_colon contains another compound keyword at start, skip
            has_nested = False
            for nkw in ("if ", "for ", "while ", "with ", "try:", "except ", "finally:"):
                if after_colon.startswith(nkw):
                    has_nested = True
                    break
            # Also check for `else:` which might follow `if cond: stmt; else: stmt2`
            # We need to handle this case where semicolons separate compound statements

            if has_nested:
                # This is like: `if a: if b: stmt` - skip, let the inner keyword handle it
                break

            # Split the line after the colon
            new_line = stripped[:colon_pos+1]
            body = after_colon
            result[i] = indent + new_line.lstrip()
            # Add body on next line with extra indent
            body_indent = indent + "    "
            # Split on semicolons for E702-style compound
            body_parts = _split_semicolons_not_in_string(body)
            body_lines = [body_indent + part.strip() for part in body_parts]
            result.insert(i+1, "\n".join(body_lines))
            break  # only handle first compound keyword per line

    return "\n".join(result)


def _split_semicolons_not_in_string(s: str) -> list[str]:
    """Split string on semicolons that are not inside quotes."""
    parts = []
    current = []
    in_s = False
    sc = None
    for ch in s:
        if not in_s:
            if ch in ("'", '"'):
                in_s = True
                sc = ch
                current.append(ch)
            elif ch == ";":
                parts.append("".join(current))
                current = []
            else:
                current.append(ch)
        else:
            current.append(ch)
            if ch == sc:
                in_s = False
                sc = None
    parts.append("".join(current))
    return parts


def main():
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        files = [line.strip() for line in sys.stdin if line.strip()]

    for filepath in files:
        if not filepath.endswith(".py"):
            continue
        try:
            with open(filepath) as f:
                original = f.read()
            fixed = fix_e701_e702(original)
            if fixed != original:
                with open(filepath, "w") as f:
                    f.write(fixed)
                print(f"Fixed: {filepath}")
        except Exception as e:
            print(f"Error {filepath}: {e}", file=sys.stderr)

    print("Done!")


if __name__ == "__main__":
    main()
