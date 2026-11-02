"""输出脱敏（ticket 09；SRS-NFR-007「日志、错误、CI 输出不得包含凭据」）。

**为什么需要**：`lint` / `doctor` 会把**用户项目里的路径与文件名**原样写进结果
（例如孤儿 Spec 的目录名、缺失的产物路径）。如果用户把密钥写进了文件名或目录名
（`token=abcd1234.cfg`、`password=hunter2/`），这些能力就会变成凭据泄露通道。
所以所有面向人/CI 的输出在**打印前**统一过一遍脱敏。

做法刻意保守、只做文本替换，不读取环境变量、不访问 `~`：

- PEM 私钥块 → `[REDACTED-PRIVATE-KEY]`
- `token=` / `password=` / `secret=` / `api_key=` 等赋值 → 值替换为 `[REDACTED]`
- AWS Access Key ID（`AKIA…`）与 `AWS_SECRET_ACCESS_KEY` 赋值 → `[REDACTED]`

**这是最后一道兜底，不是加密**：脱敏只减少「无意回显」，不改变任何文件内容，
也不承诺覆盖所有凭据形态（见 09-report.md 的遗留问题）。
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"
REDACTED_KEY = "[REDACTED-PRIVATE-KEY]"

# 凭据敏感键名（赋值形态：`key=value`、`key: value`、`"key": "value"`）
_SENSITIVE_KEYS = (
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "access_key", "secret_key", "private_key", "client_secret",
    "credential", "credentials", "authorization", "bearer",
)

_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    (?P<key>[A-Za-z0-9_.\-]*(?:%s)[A-Za-z0-9_.\-]*)   # 含敏感词的键名
    (?P<sep>\s*[:=]\s*)                               # = 或 : 分隔
    (?P<value>\"[^\"]*\"|'[^']*'|[^\s,;&)\]}]+)       # 值（引号或裸值）
    """ % "|".join(_SENSITIVE_KEYS)
)

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)

_AWS_KEY_ID_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")

# 先做一次廉价预筛：没有这些子串就不要跑正则（`req list` 这类大输出很常见）
_FAST_TOKENS = ("-----BEGIN", "AKIA", "ASIA") + tuple(
    token for token in ("token", "Token", "TOKEN", "password", "Password", "PASSWORD",
                        "secret", "Secret", "SECRET", "pwd", "apikey", "api_key",
                        "access_key", "private_key", "credential")
)


def needs_redaction(text: str) -> bool:
    return any(token in text for token in _FAST_TOKENS)


def redact_text(text: str) -> str:
    """把一段文本里的凭据形态替换成占位符。无命中时原样返回（确定性、幂等）。"""
    if not text or not needs_redaction(text):
        return text
    result = _PRIVATE_KEY_RE.sub(REDACTED_KEY, text)
    result = _AWS_KEY_ID_RE.sub(REDACTED, result)

    def _replace(match: re.Match[str]) -> str:
        return f"{match.group('key')}{match.group('sep')}{REDACTED}"

    result = _ASSIGNMENT_RE.sub(_replace, result)
    return result


def redact_value(value: Any) -> Any:
    """递归脱敏（dict/list/str）；非字符串原样返回。"""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    return value


def redact_payload(payload: Any) -> Any:
    """结果信封级脱敏：敏感键名（如 `password`/`token`）的值整段替换，其余按文本规则处理。"""
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for key, item in payload.items():
            lowered = str(key).lower()
            if any(token in lowered for token in _SENSITIVE_KEYS) and isinstance(
                    item, (str, int, float)) and item not in ("", None):
                cleaned[key] = REDACTED
                continue
            cleaned[key] = redact_payload(item)
        return cleaned
    if isinstance(payload, (list, tuple)):
        return [redact_payload(item) for item in payload]
    if isinstance(payload, str):
        return redact_text(payload)
    return payload


def redact_json_text(text: str) -> str:
    """对已经序列化好的 JSON 文本脱敏（避免重复遍历大结构体）。"""
    return redact_text(text)
