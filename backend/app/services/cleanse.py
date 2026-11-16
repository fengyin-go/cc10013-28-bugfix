"""数据清洗业务规则：规则版本固定、批次生命周期与清洗审计都收在这里。

核心约定（任何接口都不能破坏）：
1. 每个批次启动时把规则完整快照与规则版本固定到批次上，之后只用快照复算；
   规则被编辑、停用、反复切换都不会改动已存在批次的版本与历史清洗值。
2. 停用规则只阻止后续新批次，已经启动的批次保留原版本继续处理；
   停用瞬间仍在处理中的批次会进“清洗审计待处理区”暂存，不丢历史。
3. 重复提交/重复切换是幂等的：已完成批次重复提交直接返回原记录，绝不重算覆盖。
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

from app.seed import SEED_ROWS
from app.services.cleanse_engine import apply_rule, snapshot_rule
from app.store import store

MODULE = "cleanse"
BATCH_MODULE = "cleanse_batch"
AUDIT_MODULE = "cleanse_audit"
DATASET_MODULE = "cleanse_dataset"

# 规则全字段：保存时这些字段都要保留，缺失项给默认值，避免去重键/格式/空值/版本被静默丢掉。
RULE_FIELDS = ["规则编号", "目标字段", "空值替换", "格式规范化", "异常值剔除", "去重键", "规则版本", "生效日期"]
REQUIRED_FIELDS = ["规则编号", "目标字段", "空值替换"]
# 非必填字段的默认值
FIELD_DEFAULTS = {
    "格式规范化": "去空格",
    "异常值剔除": "",
    "去重键": "",
    "规则版本": "v1",
    "生效日期": "",
}
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用规则": "已启用", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]
DISABLED_STATUS = "已停用"

# 批次状态：运行中可被停用动作转入审计区；已中断可从原批次续做；已完成不可再改写。
BATCH_RUNNING = "运行中"
BATCH_INTERRUPTED = "已中断"
BATCH_FINISHED = "已完成"
ACTIVE_BATCH_STATUSES = {BATCH_RUNNING, BATCH_INTERRUPTED}
AUDIT_PENDING = "待处理"

# 清洗数据集更新节点：沿用现有取数逻辑，前端按这个节点定位刷新位置。
DATASET_UPDATE_NODE = {
    "模块": MODULE,
    "数据集": DATASET_MODULE,
    "取数入口": "/api/cleanse/dataset",
    "更新节点": "/api/cleanse/dataset/restore",
    "说明": "清洗数据集按批次启动时固定的规则快照取数，规则切换不回溯改写已固定批次。",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _bump_version(version: str) -> str:
    """规则内容保存为新版本：识别尾部数字序号递增，识别不了就追加 .1。"""
    text = str(version or "").strip() or "v1"
    digits = ""
    for char in reversed(text):
        if char.isdigit():
            digits = char + digits
        else:
            break
    if not digits:
        return f"{text}.1"
    head = text[: len(text) - len(digits)]
    return f"{head}{int(digits) + 1}"


class CleanseService:
    # ------------------------------------------------------------------ 规则
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": _next_id(rows)}
        # 全字段落库：提交了用提交值，没提交用默认值，杜绝保存时丢字段。
        for field in RULE_FIELDS:
            if field in values and str(values.get(field) or "").strip() != "":
                entry[field] = values.get(field)
            else:
                entry[field] = FIELD_DEFAULTS.get(field, "")
        if not str(entry.get("生效日期") or "").strip():
            entry["生效日期"] = datetime.now().strftime("%Y-%m-%d")
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """保存规则编辑：保留未提交字段，规则版本递增，已停用规则不允许直接改。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"清洗规则 {entry_id} 不存在或已归档"
        if entry.get("status") == DISABLED_STATUS:
            return None, "规则已停用，不能再编辑保存；请重新启用后再调整"
        # 只更新本次提交里出现的业务字段，其它字段原样保留。
        changed = False
        for field in RULE_FIELDS:
            if field in values and field != "规则版本":
                new_value = values.get(field)
                if str(entry.get(field, "") or "") != str(new_value or ""):
                    changed = True
                entry[field] = new_value
        if changed:
            entry["规则版本"] = _bump_version(str(entry.get("规则版本", "v1")))
        return entry, "清洗规则已保存并生成新版本（历史批次仍固定原版本）"

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"清洗规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据清洗可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if action == "停用规则":
            return self._disable_rule(entry)
        if action == "启用规则":
            # 重新启用只放开新批次入口，不触碰任何历史批次与审计暂存记录。
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = False
            return entry, "清洗规则已重新启用，仅对新批次生效，历史批次版本不变"

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        return entry, f"清洗规则已{action}"

    def _disable_rule(self, rule: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """停用规则：只阻止新批次；处理中的旧批次进审计待处理区，历史结果保留原版本。"""
        rule_id = int(rule["id"])
        rule["status"] = DISABLED_STATUS
        rule["pending"] = True
        rule["abnormal"] = True

        conflict_count = 0
        for batch in store.rows(BATCH_MODULE):
            if int(batch.get("规则ID", 0)) != rule_id:
                continue
            if batch.get("状态") in ACTIVE_BATCH_STATUSES:
                # 批次固定的规则版本与快照原样保留，只在审计区暂存停用冲突。
                batch["审计状态"] = AUDIT_PENDING
                self._stage_audit(batch, rule)
                conflict_count += 1

        if conflict_count:
            message = (
                f"规则已停用，新批次已被阻止；{conflict_count} 个处理中批次固定原版本，"
                "已转入清洗审计待处理区，处理优先级待定"
            )
        else:
            message = "规则已停用，仅阻止新批次；历史批次保留原规则版本与清洗结果"
        return rule, message

    def _stage_audit(self, batch: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
        audit_rows = store.rows(AUDIT_MODULE)
        # 幂等：同一批次重复停用/切换只保留一条待处理记录。
        for existing in audit_rows:
            if int(existing.get("批次ID", 0)) == int(batch["id"]) and existing.get("审计状态") == AUDIT_PENDING:
                return existing
        record = {
            "id": _next_id(audit_rows),
            "批次ID": int(batch["id"]),
            "批次编号": batch.get("批次编号", ""),
            "规则ID": int(rule["id"]),
            "规则编号": rule.get("规则编号", ""),
            "规则版本": batch.get("规则版本", ""),
            "批次状态": batch.get("状态", ""),
            "审计状态": AUDIT_PENDING,
            "冲突原因": "停用清洗规则时批次仍在处理中，批次固定原版本待人工确认",
            "处理优先级": "待定",
            "创建时间": _now(),
        }
        audit_rows.append(record)
        return record

    # ------------------------------------------------------------------ 批次
    def list_batches(
        self,
        *,
        rule_id: int | None = None,
        status: str | None = None,
        audit_status: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(BATCH_MODULE)
        if rule_id is not None:
            rows = [row for row in rows if int(row.get("规则ID", 0)) == rule_id]
        if status:
            rows = [row for row in rows if row.get("状态") == status]
        if audit_status:
            rows = [row for row in rows if row.get("审计状态") == audit_status]
        return rows

    def get_batch(self, batch_id: int) -> dict[str, Any] | None:
        return store.find(BATCH_MODULE, batch_id)

    def start_batch(self, rule_id: int) -> tuple[dict[str, Any] | None, str]:
        """启动新批次：只允许启用中的规则；批次立刻固定规则快照与版本。"""
        rule = store.find(MODULE, rule_id)
        if rule is None:
            return None, f"清洗规则 {rule_id} 不存在或已归档"
        if rule.get("status") == DISABLED_STATUS:
            return None, "清洗规则已停用，停用只阻止新批次：不能再基于该规则启动新批次"
        if rule.get("status") != "已启用":
            return None, f"规则当前状态为「{rule.get('status')}」，需先启用才能启动清洗批次"

        dataset = [dict(row) for row in store.rows(DATASET_MODULE)]
        snapshot = snapshot_rule(rule)
        cleaned, metrics = apply_rule(dataset, snapshot)
        batches = store.rows(BATCH_MODULE)
        batch = {
            "id": _next_id(batches),
            "批次编号": f"BATCH-CL-{len(batches) + 1:04d}",
            "规则ID": int(rule["id"]),
            "规则编号": rule.get("规则编号", ""),
            # 一个批次只能固定一个规则版本：版本与快照在此刻写死，后续不再随规则变化。
            "规则版本": snapshot["规则版本"],
            "规则快照": snapshot,
            "状态": BATCH_RUNNING,
            "待处理": True,
            "原始记录": dataset,
            "清洗结果": cleaned,
            "指标": metrics,
            "启动时间": _now(),
            "更新时间": _now(),
            "审计状态": "",
        }
        batches.append(batch)
        return batch, f"批次已启动并固定规则版本 {snapshot['规则版本']}"

    def submit_batch(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        """提交批次结果：幂等。已完成批次重复提交直接回原记录，绝不重新计算覆盖历史。"""
        batch = store.find(BATCH_MODULE, batch_id)
        if batch is None:
            return None, f"清洗批次 {batch_id} 不存在或已归档"
        if batch.get("状态") == BATCH_FINISHED:
            return batch, "批次已完成，重复提交不改写历史，仍返回原规则版本与清洗结果"

        # 用批次自己固定的快照复算，结果与启动时一致，证明规则切换没有污染本批次。
        cleaned, metrics = apply_rule(
            [dict(row) for row in batch.get("原始记录", [])],
            batch.get("规则快照", {}),
        )
        batch["清洗结果"] = cleaned
        batch["指标"] = metrics
        batch["状态"] = BATCH_FINISHED
        batch["待处理"] = False
        batch["更新时间"] = _now()
        if batch.get("审计状态") == AUDIT_PENDING:
            # 从审计待处理区完成的批次，审计记录同步标记为已随批次完结。
            batch["审计状态"] = ""
            self._resolve_audit(int(batch["id"]))
        return batch, f"批次已按固定版本 {batch.get('规则版本')} 完成，历史清洗值已锁定"

    def interrupt_batch(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        """规则切换中断：批次保留固定版本与已清洗数据，标记可从原批次续做。"""
        batch = store.find(BATCH_MODULE, batch_id)
        if batch is None:
            return None, f"清洗批次 {batch_id} 不存在或已归档"
        if batch.get("状态") == BATCH_FINISHED:
            return None, "批次已完成，历史结果已锁定，不能中断"
        if batch.get("状态") == BATCH_INTERRUPTED:
            return batch, "批次已是中断状态，固定规则版本未变，可从原批次续做"
        batch["状态"] = BATCH_INTERRUPTED
        batch["待处理"] = True
        batch["更新时间"] = _now()
        return batch, f"批次已中断，仍固定规则版本 {batch.get('规则版本')}，可从原批次续做"

    def resume_batch(self, batch_id: int) -> tuple[dict[str, Any] | None, str]:
        """从原批次续做：不新建批次、不换规则版本，沿用固定快照继续处理。"""
        batch = store.find(BATCH_MODULE, batch_id)
        if batch is None:
            return None, f"清洗批次 {batch_id} 不存在或已归档"
        if batch.get("状态") != BATCH_INTERRUPTED:
            return None, f"批次当前状态为「{batch.get('状态')}」，只有中断批次可以续做"
        batch["状态"] = BATCH_RUNNING
        batch["待处理"] = True
        batch["更新时间"] = _now()
        return batch, f"已从原批次续做，规则版本仍固定为 {batch.get('规则版本')}"

    # ------------------------------------------------------------------ 审计
    def list_audit(self, audit_status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(AUDIT_MODULE)
        if audit_status:
            rows = [row for row in rows if row.get("审计状态") == audit_status]
        return rows

    def _resolve_audit(self, batch_id: int) -> None:
        for record in store.rows(AUDIT_MODULE):
            if int(record.get("批次ID", 0)) == batch_id and record.get("审计状态") == AUDIT_PENDING:
                record["审计状态"] = "已完结"
                record["处理优先级"] = "已处理"

    # ---------------------------------------------------------------- 数据集
    def get_dataset_node(self) -> dict[str, Any]:
        """按现有取数逻辑返回清洗数据集与更新节点定位信息。"""
        rows = store.rows(DATASET_MODULE)
        node = dict(DATASET_UPDATE_NODE)
        node["total"] = len(rows)
        node["items"] = [dict(row) for row in rows]
        return node

    def restore_dataset(self) -> tuple[dict[str, Any] | None, str]:
        """恢复清洗数据集到初始取数基线；已固定批次的历史结果不受影响、仍可对得上。"""
        baseline = copy.deepcopy(SEED_ROWS.get(DATASET_MODULE, []))
        table = store.rows(DATASET_MODULE)
        table.clear()
        table.extend(copy.deepcopy(baseline))
        node = self.get_dataset_node()
        return node, "清洗数据集已恢复到初始基线，历史批次仍按各自固定规则版本保留清洗结果"
