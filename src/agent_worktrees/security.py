from __future__ import annotations

import re
from pathlib import Path


SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\b(?:sk|sk-proj|sk-ant|ctx7sk)-[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"(?i)(?:[a-z0-9]+[_-])*(?:api[_-]?key|secret[_-]?key|access[_-]?token|"
        r"session[_-]?token|api[_-]?token|auth[_-]?token|client[_-]?secret|"
        r"private[_-]?key|secret|password)\s*[:=]\s*(?:"
        r"['\"][A-Za-z0-9_+/=.:-]{16,}['\"]|[A-Za-z0-9_+/=.:-]{16,}"
        r")(?=$|[\s,;}#])"
    ),
    re.compile(r"(?i)authorization\s*[:=]\s*['\"]?bearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\b(?:postgres|mysql|mongodb|redis)://[^\s/:]+:[^\s/@]+@"),
)

SENSITIVE_PATH_PATTERN = re.compile(
    r"(?i)(^|/)(?:"
    r"\.env(?:\..*)?|"
    r"\.npmrc|\.pypirc|\.netrc|"
    r"id_(?:rsa|ed25519|ecdsa|dsa)(?:\.pub)?|"
    r"credentials?(?:\.[^/]*)?|auth\.json|"
    r"service[-_]?account(?:\.[^/]*)?|"
    r"kubeconfig|"
    r".*\.(?:pem|p12|pfx|key)"
    r")$"
)


def contains_secret(text: str) -> bool:
    placeholders = ("your-", "your_", "example", "placeholder", "changeme", "redacted")
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if any(marker in match.group(0).lower() for marker in placeholders):
                continue
            return True
    return False


def sensitive_path(path: str) -> bool:
    if path.lower().rsplit("/", 1)[-1] in {".env.example", ".env.sample", ".env.template"}:
        return False
    return SENSITIVE_PATH_PATTERN.search(path) is not None


def has_symlink_component(base: Path, candidate: Path) -> bool:
    try:
        relative = candidate.absolute().relative_to(base.absolute())
    except ValueError:
        return True
    current = base.absolute()
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def safe_repository_path(root: Path, relative: str) -> bool:
    specification = Path(relative)
    if specification.is_absolute() or ".." in specification.parts:
        return False
    candidate = root / specification
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return False
    return not has_symlink_component(root, candidate)
