from __future__ import annotations

import json
from pathlib import Path


def _strip_json_comments(raw: str) -> str:
    result: list[str] = []
    in_string = False
    string_char = ""
    escape = False
    index = 0
    length = len(raw)

    while index < length:
        char = raw[index]
        nxt = raw[index + 1] if index + 1 < length else ""

        if in_string:
            result.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == string_char:
                in_string = False
            index += 1
            continue

        if char in {'"', "'"}:
            in_string = True
            string_char = char
            result.append(char)
            index += 1
            continue

        if char == "/" and nxt == "/":
            index += 2
            while index < length and raw[index] not in "\r\n":
                index += 1
            continue

        if char == "/" and nxt == "*":
            index += 2
            while index + 1 < length and not (raw[index] == "*" and raw[index + 1] == "/"):
                index += 1
            index += 2
            continue

        result.append(char)
        index += 1

    return "".join(result)


def _remove_trailing_commas(raw: str) -> str:
    result: list[str] = []
    in_string = False
    string_char = ""
    escape = False
    index = 0
    length = len(raw)

    while index < length:
        char = raw[index]

        if in_string:
            result.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == string_char:
                in_string = False
            index += 1
            continue

        if char in {'"', "'"}:
            in_string = True
            string_char = char
            result.append(char)
            index += 1
            continue

        if char == ",":
            lookahead = index + 1
            while lookahead < length and raw[lookahead].isspace():
                lookahead += 1
            if lookahead < length and raw[lookahead] in "]}":
                index += 1
                continue

        result.append(char)
        index += 1

    return "".join(result)


def load_jsonish_text(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        cleaned = _remove_trailing_commas(_strip_json_comments(raw))
        return json.loads(cleaned or "{}")


def load_jsonish(path: Path) -> dict:
    if not path.exists():
        return {}
    return load_jsonish_text(path.read_text(encoding="utf-8"))


def dump_jsonish(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
