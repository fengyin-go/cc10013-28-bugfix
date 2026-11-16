"""清洗执行引擎：纯函数实现，不依赖内存仓库，方便按规则快照复算。

设计要点：
- 入参里的 rule_snapshot 是批次启动那一刻的规则完整快照，引擎只读快照、不读当前规则，
  因此规则后续被编辑、停用、重复切换都不会影响已经启动的批次。
- 同样的快照 + 同样的原始数据，多次执行结果必须完全一致，保证重复提交不会改写历史。
"""
from __future__ import annotations

import re
from typing import Any

# 清洗结果里需要稳定保留的规则字段，快照里缺省时用空串兜底，避免 KeyError。
SNAPSHOT_FIELDS = [
    "规则编号",
    "目标字段",
    "空值替换",
    "格式规范化",
    "异常值剔除",
    "去重键",
    "规则版本",
    "生效日期",
]


def snapshot_rule(rule: dict[str, Any]) -> dict[str, str]:
    """从规则记录复制一份不可变快照，批次启动后只认这份快照。"""
    return {field: str(rule.get(field, "") or "") for field in SNAPSHOT_FIELDS}


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _normalize_format(value: Any, format_option: str) -> tuple[str, bool]:
    """按格式规范化策略处理单个值，返回 (新值, 是否发生变化)。

    策略是关键词驱动的确定性规则，不依赖外部库：
    - 含“数字”/“手机号”：只保留数字字符；
    - 含“去空格”/“空格”：去除首尾空格并压缩中间空白；
    - 默认同样做去空格处理，保证历史结果可复算。
    """
    text = "" if value is None else str(value)
    normalized = text.strip()
    if "数字" in format_option or "手机号" in format_option:
        normalized = re.sub(r"\D", "", normalized)
    else:
        normalized = re.sub(r"\s+", " ", normalized)
    return normalized, normalized != text


def _is_outlier(value: Any, outlier_option: str) -> bool:
    """按异常值剔除策略判断当前值是否应被剔除，关键词匹配，空白值不在此环节处理。"""
    if not outlier_option or _is_blank(value):
        return False
    try:
        numeric = float(str(value).strip())
    except ValueError:
        return False
    if "负数" in outlier_option and numeric < 0:
        return True
    if "零" in outlier_option and numeric == 0:
        return True
    return False


def apply_rule(
    rows: list[dict[str, Any]], rule_snapshot: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """按规则快照清洗数据集，返回 (清洗后记录, 过程指标)。

    处理顺序固定：格式规范化 → 空值替换 → 异常值剔除 → 去重，
    每个环节都计数，便于批次抽屉里核对历史值。
    """
    target = str(rule_snapshot.get("目标字段", "") or "").strip()
    null_replacement = str(rule_snapshot.get("空值替换", "") or "")
    format_option = str(rule_snapshot.get("格式规范化", "") or "")
    outlier_option = str(rule_snapshot.get("异常值剔除", "") or "")
    dedup_key = str(rule_snapshot.get("去重键", "") or "").strip()

    metrics = {
        "输入行数": len(rows),
        "格式规范化数": 0,
        "空值替换数": 0,
        "异常值剔除数": 0,
        "去重剔除数": 0,
        "输出行数": 0,
    }

    staged: list[dict[str, Any]] = []
    for raw in rows:
        record = dict(raw)
        if target and target in record:
            raw_value = record[target]
            # 异常值剔除要在格式规范化之前判断：纯数字规范化会把负号一并去掉，
            # 先判断才能正确识别负数等异常原始值。
            if _is_outlier(raw_value, outlier_option):
                metrics["异常值剔除数"] += 1
                continue
            # 1. 格式规范化
            value, changed = _normalize_format(raw_value, format_option)
            if changed:
                metrics["格式规范化数"] += 1
            # 2. 空值策略：空值统一替换为规则里配置的值
            if _is_blank(value):
                value = null_replacement
                metrics["空值替换数"] += 1
            record[target] = value
        staged.append(record)

    # 4. 去重：按去重键保留首次出现的记录，重复记录计数后丢弃
    cleaned: list[dict[str, Any]] = []
    if dedup_key:
        seen: set[str] = set()
        for record in staged:
            fingerprint = str(record.get(dedup_key, "") or "")
            if fingerprint in seen:
                metrics["去重剔除数"] += 1
                continue
            seen.add(fingerprint)
            cleaned.append(record)
    else:
        cleaned = staged

    metrics["输出行数"] = len(cleaned)
    return cleaned, metrics
