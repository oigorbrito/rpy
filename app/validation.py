from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

_DIGITS_11_14_RE = re.compile(r"(?<!\d)\d{11}(?:\d{3})?(?!\d)")
_CNJ_RE = re.compile(r"\b\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}\b")
# Long unbroken numeric runs (protocol numbers, legacy autos numbers, unformatted
# foreign identifiers) that do not match CNJ format still must match the payload
# code. Shorter runs are not inspected to avoid false positives from dates, years,
# amounts and small legible counts.
_LONG_DIGITS_RE = re.compile(r"(?<!\d)\d{11,}(?!\d)")
_CLASS_RE = re.compile(r"\bclass\s*=", re.IGNORECASE)
_FORECAST_RE = re.compile(
    r"provavelmente\s+ser[áa]\s+condenad|chances?\s+de|tende\s+a\s+ganhar|recomendo\s+que",
    re.IGNORECASE,
)
_PARTY_TAG_RE = re.compile(r"<Party\s+name=[\"']([^\"']+)[\"'][^>]*/?>", re.IGNORECASE)
_ROLE = r"autor(?:a)?|r[ée]u|requerente|requerid[oa]|exequente|executad[oa]"
_CAPITALIZED_TOKEN = r"[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][A-Za-zÁÀÂÃÉÊÍÓÔÕÚÇáàâãéêíóôõúç0-9&.'/-]*"
_CONNECTOR = r"(?:da|de|do|das|dos|e)"
_PROPER_NAME = rf"{_CAPITALIZED_TOKEN}(?:\s+(?:{_CONNECTOR}\s+)?{_CAPITALIZED_TOKEN}){{1,7}}"
_ROLE_NAME_RE = re.compile(
    rf"\b(?:(?i:o|a)\s+)?(?:(?i:{_ROLE}))\s*"
    rf"(?:(?::|[-–—])\s*|(?:(?i:é|foi|seria|denominad[oa]|identificad[oa]\s+como))\s+)?"
    rf"(?P<name>{_PROPER_NAME})"
)
_NAME_ROLE_RE = re.compile(
    rf"\b(?P<name>{_PROPER_NAME})\s*,\s*"
    rf"(?:(?i:na\s+qualidade\s+de|como)\s+)?(?:(?i:{_ROLE}))\b"
)
_JSX_TAG_RE = re.compile(r"<(/?)([A-Z][A-Za-z0-9]*)(?:\s[^<>]*?)?(/?)>")


@dataclass(slots=True)
class ValidationResult:
    passed: bool
    errors: list[str]


def _normalize_digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _normalize_party_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(character for character in decomposed if not unicodedata.combining(character))
    words = re.findall(r"[\w]+", without_marks.casefold(), flags=re.UNICODE)
    return " ".join(words)


def _party_names(parties: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for party in parties:
        name = party.get("name") or party.get("nome")
        if name:
            normalized = _normalize_party_name(str(name))
            if normalized:
                names.add(normalized)
    return names


def _mentioned_party_names(text: str) -> set[str]:
    raw_names = set(_PARTY_TAG_RE.findall(text))
    raw_names.update(match.group("name") for match in _ROLE_NAME_RE.finditer(text))
    raw_names.update(match.group("name") for match in _NAME_ROLE_RE.finditer(text))
    return {
        normalized
        for name in raw_names
        if (normalized := _normalize_party_name(name))
    }


def _jsx_errors(text: str) -> list[str]:
    errors: list[str] = []
    if _CLASS_RE.search(text):
        errors.append("JSX must use className= instead of class=")

    stack: list[str] = []
    for match in _JSX_TAG_RE.finditer(text):
        closing, tag, self_closing = match.groups()
        if self_closing:
            continue
        if closing:
            if not stack or stack[-1] != tag:
                errors.append(f"unbalanced JSX tag: {tag}")
                continue
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        errors.append(f"unclosed JSX tags: {', '.join(stack)}")
    return errors


def validar(*, text: str, code: str, parties: list[dict[str, Any]]) -> ValidationResult:
    errors: list[str] = []

    if _DIGITS_11_14_RE.search(text):
        errors.append("possible unmasked CPF/CNPJ")

    expected_cnj = _normalize_digits(code)
    for found in _CNJ_RE.findall(text):
        if _normalize_digits(found) != expected_cnj:
            errors.append(f"CNJ mismatch: {found}")

    # Any long numeric reference that is not the exact payload code indicates a
    # hallucinated/foreign process identifier, even without CNJ separators.
    for found in _LONG_DIGITS_RE.findall(text):
        if _normalize_digits(found) != expected_cnj:
            errors.append(f"CNJ mismatch: {found}")

    allowed_parties = _party_names(parties)
    mentioned_names = _mentioned_party_names(text)
    unknown = sorted(name for name in mentioned_names if name not in allowed_parties)
    if unknown:
        errors.append("hallucinated parties: " + ", ".join(unknown))

    if _FORECAST_RE.search(text):
        errors.append("prognostic language is prohibited")

    errors.extend(_jsx_errors(text))
    return ValidationResult(passed=not errors, errors=errors)
