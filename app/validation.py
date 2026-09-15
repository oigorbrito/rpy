from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_DIGITS_11_14_RE = re.compile(r"(?<!\d)\d{11}(?:\d{3})?(?!\d)")
_CNJ_RE = re.compile(r"\b\d{7}-?\d{2}\.?\d{4}\.?\d\.?\d{2}\.?\d{4}\b")
_CLASS_RE = re.compile(r"\bclass\s*=", re.IGNORECASE)
_FORECAST_RE = re.compile(
    r"provavelmente\s+ser[áa]\s+condenad|chances?\s+de|tende\s+a\s+ganhar|recomendo\s+que",
    re.IGNORECASE,
)
_PARTY_TAG_RE = re.compile(r"<Party\s+name=[\"']([^\"']+)[\"'][^>]*/?>", re.IGNORECASE)
_ROLE_NAME_RE = re.compile(
    r"\b(?:autor(?:a)?|r[ée]u|requerente|requerido|exequente|executado)\s*[:\-]\s*"
    r"([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\wÁÀÂÃÉÊÍÓÔÕÚÇáàâãéêíóôõúç.'-]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][\wÁÀÂÃÉÊÍÓÔÕÚÇáàâãéêíóôõúç.'-]+)+)",
    re.IGNORECASE,
)
_JSX_TAG_RE = re.compile(r"<(/?)([A-Z][A-Za-z0-9]*)(?:\s[^<>]*?)?(/?)>")


@dataclass(slots=True)
class ValidationResult:
    passed: bool
    errors: list[str]


def _normalize_digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _party_names(parties: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for party in parties:
        name = party.get("name") or party.get("nome")
        if name:
            names.add(str(name).strip().casefold())
    return names


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

    allowed_parties = _party_names(parties)
    mentioned_names = {name.strip().casefold() for name in _PARTY_TAG_RE.findall(text)}
    mentioned_names.update(name.strip().casefold() for name in _ROLE_NAME_RE.findall(text))
    unknown = sorted(name for name in mentioned_names if name and name not in allowed_parties)
    if unknown:
        errors.append("hallucinated parties: " + ", ".join(unknown))

    if _FORECAST_RE.search(text):
        errors.append("prognostic language is prohibited")

    errors.extend(_jsx_errors(text))
    return ValidationResult(passed=not errors, errors=errors)
