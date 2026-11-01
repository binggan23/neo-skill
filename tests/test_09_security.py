"""ticket 09：凭据与硬编码路径检查（SRS-NFR-007）。

两层：

1. **行为层（强）**：把凭据形态写进项目里的**文件名与目录名**，让 `lint` / `doctor`
   必须回显这些路径（孤儿 Spec、缺失产物），断言 stdout/stderr/JSON 都**不原样回显**
   凭据原文。
2. **源码层（弱）**：grep 运行时代码，断言没有硬编码 `~/.claude`、`~/.netrc` 等
   个人凭据路径，也不主动读环境变量里的 token。**这是弱检查**：它只能发现明显的
   字符串出现，无法证明运行时一定不读凭据（见 09-report.md 遗留问题）。
"""

from __future__ import annotations

from pathlib import Path

from conftest import RUNTIME_DIR, read, run_cli, write
from helpers_03 import ENV, TEAM

PRIVATE_KEY_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA0fakefakefakefakefakefakefakefakefakefake\n"
    "-----END RSA PRIVATE KEY-----\n"
)

# 每条都是「像真的凭据」，断言它们一个字都不能出现在输出里
SECRET_VALUES = (
    "s3cr3t-token-9142",
    "hunter2-password-1234",
    "wJalrXUtnFEMI-K7MDENG-bPxRfiCYEXAMPLEKEY",
    "AKIAIOSFODNN7EXAMPLE",
    "qawsedrf-private-key-body",
)

TOKEN_DIR = "06-specs/resume/PRD-777-token=s3cr3t-token-9142"
PASSWORD_DIR = "archive/password=hunter2-password-1234"
SECRET_FILES = (
    f"{TOKEN_DIR}/spec.md",
    f"{PASSWORD_DIR}/notes.md",
    "docs/AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI-K7MDENG-bPxRfiCYEXAMPLEKEY.txt",
    "docs/credentials-AKIAIOSFODNN7EXAMPLE.env",
)


def inited(project: Path) -> Path:
    assert run_cli(project, "init").code == 0
    write(project / "00-management/team.md", TEAM)
    return project


def write_secrets(project: Path) -> Path:
    """凭据出现在目录名、文件名与正文里（真实事故就是这样发生的）。"""
    for rel in SECRET_FILES:
        write(project / rel, PRIVATE_KEY_BLOCK + "\ntoken=s3cr3t-token-9142\n")
    return project


def assert_no_secret_leak(text: str, *, where: str) -> None:
    for secret in SECRET_VALUES:
        assert secret not in text, f"{where} 里回显了凭据原文：{secret}"
    assert "BEGIN RSA PRIVATE KEY" not in text, f"{where} 里回显了私钥块"
    assert "hunter2-password" not in text


# --------------------------------------------------------------------------
# 行为层
# --------------------------------------------------------------------------
def test_lint_json_does_not_echo_secrets(project: Path):
    inited(project)
    write_secrets(project)
    result = run_cli(project, "lint", env=ENV)
    assert result.code == 1, result.stdout[-2000:]     # 孤儿 Spec 是阻断项
    assert_no_secret_leak(result.stdout, where="lint --json stdout")
    assert_no_secret_leak(result.stderr, where="lint --json stderr")
    # 脱敏后仍然是有用的诊断：错误码与对象都在
    assert "ORPHAN_SPEC" in result.stdout
    assert "[REDACTED]" in result.stdout


def test_lint_human_output_does_not_echo_secrets(project: Path):
    inited(project)
    write_secrets(project)
    result = run_cli(project, "lint", json_output=False, env=ENV)
    assert result.code == 1, result.stdout[-2000:]
    assert_no_secret_leak(result.stdout, where="lint 人类输出")
    assert_no_secret_leak(result.stderr, where="lint 人类输出 stderr")


def test_doctor_output_does_not_echo_secrets(project: Path):
    inited(project)
    write_secrets(project)
    result = run_cli(project, "doctor", env=ENV)
    assert_no_secret_leak(result.stdout, where="doctor --json stdout")
    assert_no_secret_leak(result.stderr, where="doctor --json stderr")


def test_prd_list_does_not_echo_secrets_in_paths(project: Path):
    """PRD 目录名带凭据形态时，`prd list` 的 `path` 字段也必须脱敏。"""
    inited(project)
    directory = project / "04-prds/resume/PRD-777-token=s3cr3t-token-9142"
    write(directory / "metadata.yaml",
          "id: PRD-777\nslug: creds-sample\ntitle: 凭据样例\nmodule: resume\n"
          "feature: sample\nscenarios: []\nsource_reqs: []\nrelease: v0.1.0\nstatus: DRAFT\n"
          "version: 1\ncontent_hash: null\nlocked_fields: [id]\n")
    write(directory / "prd.md", "# PRD-777\n\n" + PRIVATE_KEY_BLOCK)
    result = run_cli(project, "prd", "list", env=ENV)
    assert result.code == 0, result.stdout[-2000:]
    assert_no_secret_leak(result.stdout, where="prd list --json stdout")


def test_error_envelope_does_not_echo_secrets(project: Path):
    """错误信封也不能把凭据路径回显（例如找不到 PRD 时的 object）。"""
    inited(project)
    result = run_cli(project, "prd", "show", "--id", "PRD-777-token=s3cr3t-token-9142",
                     env=ENV)
    assert result.code == 1, result
    assert_no_secret_leak(result.stdout, where="prd show 错误信封")


# --------------------------------------------------------------------------
# 源码层（弱检查）
# --------------------------------------------------------------------------
FORBIDDEN_SNIPPETS = (
    "~/.claude",
    ".claude/credentials",
    "~/.netrc",
    ".netrc",
    "~/.aws/credentials",
    "~/.ssh/id_rsa",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
)


def runtime_sources() -> list[Path]:
    return sorted((RUNTIME_DIR / "flow_core").rglob("*.py"))


# `redact.py` 是**识别器**，它必须包含这些字面量才能做替换；其它模块一律不许出现。
DETECTOR_MODULE = "redact.py"


def test_runtime_has_no_hardcoded_credential_paths():
    """弱检查：源码里不该出现个人凭据路径或直接读 API key 环境变量。

    `redact.py` 例外：它的职责就是识别这些形态（含字符串字面量），
    但要用下面的 `test_credential_literals_are_confined_to_detector` 保证不外溢。
    """
    offenders: list[str] = []
    for path in runtime_sources():
        if path.name == DETECTOR_MODULE:
            continue
        text = read(path)
        for snippet in FORBIDDEN_SNIPPETS:
            if snippet in text:
                offenders.append(f"{path.name}: {snippet}")
    assert not offenders, "源码里出现硬编码凭据路径/环境变量：" + "、".join(offenders)


def test_runtime_does_not_read_secret_environment_variables():
    """弱检查：不主动读 `*_TOKEN`/`*_SECRET*` 这类环境变量。"""
    offenders: list[str] = []
    for path in runtime_sources():
        text = read(path)
        if path.name == DETECTOR_MODULE:
            continue
        for needle in ('environ["', "environ.get("):
            for chunk in text.split(needle)[1:]:
                key = chunk.split('"')[0].split("'")[0]
                lowered = key.lower()
                if any(word in lowered for word in ("token", "secret", "password", "api_key")):
                    offenders.append(f"{path.name}: {key}")
    assert not offenders, "运行时读取了凭据类环境变量：" + "、".join(offenders)


def test_credential_literals_are_confined_to_detector():
    """凭据形态的字符串字面量只允许出现在 `redact.py`（识别器），不许散落到别处。"""
    literals = ("AWS_SECRET_ACCESS_KEY", "PRIVATE KEY", "AKIA", "password=", "token=")
    offenders: list[str] = []
    for path in runtime_sources():
        if path.name == DETECTOR_MODULE:
            continue
        text = read(path)
        for literal in literals:
            if literal in text:
                offenders.append(f"{path.name}: {literal}")
    assert not offenders, "凭据形态字面量外溢：" + "、".join(offenders)


def test_redaction_module_exists_and_is_used_by_entrypoint():
    """脱敏是最后一道兜底：入口必须真的调用它（否则行为层测试会失效）。"""
    entrypoint = read(RUNTIME_DIR / "flow_core" / "cli.py")
    assert "redact" in entrypoint, "cli.py 的出口没有走脱敏"
    assert (RUNTIME_DIR / "flow_core" / "redact.py").is_file()
