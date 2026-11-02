"""ticket 03 测试共用夹具：团队、已排期主需求、PRD 文件读取与正文样例。

全部通过 CLI 命令搭建对象（共享设计 §10：不手写 metadata 绕过 CLI），
只有「构造违规状态」的测试才直接改文件。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from conftest import read, run_cli, write

NOW = "2026-03-01T10:00:00+08:00"
ENV = {"NEO_SKILL_NOW": NOW}

TEAM = """---
confirmed: true
confirmed_by: alice
confirmed_at: 2026-03-01T09:00:00+08:00
members:
  - {id: alice, name: 爱丽丝, roles: [requirement_owner, project_owner]}
  - {id: bob, name: 鲍勃, roles: [stakeholder]}
  - {id: carol, name: 卡罗尔, roles: [developer]}
---

# 团队与职责

成员与职责映射见 front matter。
"""

REQ_BODY = """# REQ-001 batch parse resume

## 背景

客户每月人工解析 2000 份简历，招聘旺季堆积。

## 用户

招聘专员。

## 问题

单份上传耗时，旺季需要 3 人全职处理。

## 场景

1. 专员一次上传 100 份简历；
2. 系统异步解析并给出结果清单。

## 期望结果

单批 100 份在 5 分钟内完成解析。

## 来源

客户访谈 2026-02。
"""


def setup_team(project: Path) -> Path:
    write(project / "00-management/team.md", TEAM)
    return project


def planned_req(project: Path, *, title: str = "简历批量解析", module: str = "resume",
                slug: str = "batch-parse", release: str = "v0.1.0",
                draft: Path | None = None) -> str:
    """通过 req 命令组造一条 PLANNED 主需求，返回其编号。"""
    setup_team(project)
    created = run_cli(project, "req", "create", "--title", title, "--module", module,
                      "--slug", slug)
    assert created.code == 0, created
    req_id = created.json["data"]["req"]["id"]
    body_file = draft or (project / "draft-req.md")
    write(body_file, REQ_BODY.replace("REQ-001", req_id).replace("batch parse resume", title))
    updated = run_cli(project, "req", "update", "--id", req_id, "--body-file", str(body_file))
    assert updated.code == 0, updated
    confirmed = run_cli(project, "req", "confirm", "--id", req_id, "--actor", "bob",
                        "--confirm", env=ENV)
    assert confirmed.code == 0, confirmed
    planned = run_cli(project, "req", "plan", "--id", req_id, "--actor", "alice",
                      "--confirm", "--release", release, env=ENV)
    assert planned.code == 0, planned
    return req_id


def prd_dir(project: Path, prd_id: str = "PRD-001", module: str = "resume",
            slug: str = "batch-parse") -> Path:
    return project / "04-prds" / module / f"{prd_id}-{slug}"


def prd_meta(project: Path, prd_id: str = "PRD-001", module: str = "resume",
             slug: str = "batch-parse") -> dict:
    path = prd_dir(project, prd_id, module, slug) / "metadata.yaml"
    data = yaml.safe_load(read(path))
    assert isinstance(data, dict)
    return data


def prd_text(project: Path, prd_id: str = "PRD-001", module: str = "resume",
             slug: str = "batch-parse") -> str:
    return read(prd_dir(project, prd_id, module, slug) / "prd.md")


def req_meta(project: Path, req_id: str = "REQ-001") -> dict:
    items = sorted((project / "03-requirements/items").glob(f"{req_id}-*.md"))
    assert items, f"找不到 {req_id}"
    head = read(items[0]).split("---\n", 2)[1]
    data = yaml.safe_load(head)
    assert isinstance(data, dict)
    return data


def create_prd(project: Path, *, req_id: str = "REQ-001",
               scenarios: str = "SC-RES-01,SC-RES-02", extra: tuple[str, ...] = (),
               expect: int = 0) -> dict:
    result = run_cli(project, "prd", "create", "--req", req_id, "--scenarios", scenarios,
                     *extra, env=ENV)
    assert result.code == expect, result
    return result.json


def update_body(project: Path, body: str, *, prd_id: str = "PRD-001",
                expect: int = 0) -> dict:
    path = project / "prd-body-draft.md"
    write(path, body)
    result = run_cli(project, "prd", "update", "--id", prd_id, "--body-file", str(path),
                     env=ENV)
    assert result.code == expect, result
    return result.json


VALID_PRD = """# PRD-001 简历批量解析

## 背景与目标

招聘旺季单月简历量 2000 份，人工解析需要 3 人全职，解析时延导致候选人流失。
目标：单批 100 份简历在 5 分钟内完成解析。

## 范围

- 本期做：批量上传、异步解析、结果清单导出。
- 本期不做：简历智能评分、自动排期面试、移动端上传。

## 现状与变化

新功能，无现状。

## 场景

### SC-RES-01 批量上传简历

招聘专员一次上传多份简历并看到上传进度。

**验收标准**

- AC-RES-01-01
  - Given 招聘专员已登录且被授予上传权限
  - When 一次上传 100 份简历
  - Then 上传完成后页面显示 100 份简历的待解析状态

- AC-RES-01-02
  - Given 已上传 100 份简历
  - When 上传队列开始解析
  - Then 5 分钟内 100 条解析结果全部可见

### SC-RES-02 解析失败重试

单份简历解析失败后可单独重试。

**验收标准**

- AC-RES-02-01
  - Given 某份简历解析失败并记录错误码
  - When 招聘专员点击重试
  - Then 该份简历重新进入解析队列且错误码被清除

## 非功能需求

- 单批 100 份简历解析在 5 分钟内完成，接口 P95 延迟不超过 800ms。
- 简历文件仅保存在项目内网存储，不写入外部服务。

## 数据需求

- 保存简历文件名、上传人、上传时间、解析状态与错误码，保留 90 天。

## 依赖与风险

- 依赖现有对象存储；解析服务异常时任务保留在队列，可重投。

## 待确认问题

- 解析结果的字段映射是否需要支持自定义模板？由产品负责人确认。
"""
