import re


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if re.search(r"(?i)authorization|api[_-]?key|password|cookie|secret", k)
                    else redact(v, secrets)) for k, v in value.items()}
    if isinstance(value, list): return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret: value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
        value = re.sub(r"(?i)((?:password|api[_-]?key|token|secret)=)[^\s&]+", r"\1[REDACTED]", value)
    return value
