import re


def clean_text(text: str, return_removed: bool = False):
    """Remove hashes, code blocks, and ascii art."""
    if not text:
        return ("", []) if return_removed else ""

    removed: list[str] = []
    # Remove code blocks
    def repl_block(match):
        removed.append(match.group(0))
        return ""

    text = re.sub(r'```.*?```', repl_block, text, flags=re.DOTALL)
    # Remove long hexadecimal/hash strings
    text = re.sub(r'\b[a-fA-F0-9]{32,}\b', lambda m: _append_removed(m, removed), text)
    # Remove base64-like strings
    text = re.sub(r"\b[A-Za-z0-9+/]{40,}={0,2}\b", lambda m: _append_removed(m, removed), text)

    cleaned_lines = []
    for line in text.splitlines():
        if len(re.findall(r"[\|/_\\~@#$%^&*]", line)) >= 6:
            removed.append(line)
        else:
            cleaned_lines.append(line)
    result = "\n".join(cleaned_lines)
    if return_removed:
        return result, removed
    return result


def _append_removed(match: re.Match, removed: list[str]) -> str:
    removed.append(match.group(0))
    return ""
