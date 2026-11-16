"""数据清洗接口：规则维护、规则版本固定、清洗批次与清洗审计待处理区。

路由顺序注意：固定路径（/batches、/audit、/dataset 等）必须声明在
/{entry_id} 这种参数路径之前，否则会被参数路由抢先匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.cleanse import CleanseService

router = APIRouter(prefix="/api/cleanse", tags=["数据清洗"])

service = CleanseService()

LIST_FIELDS = ["规则编号", "目标字段", "空值替换", "格式规范化", "异常值剔除", "去重键", "规则版本", "生效日期"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤数据清洗列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/batches")
def list_batches(
    rule_id: int | None = Query(default=None, description="按清洗规则过滤批次"),
    status: str | None = Query(default=None, description="运行中、已中断、已完成"),
    audit_status: str | None = Query(default=None, description="按审计状态过滤，如 待处理"),
) -> dict[str, Any]:
    """清洗批次列表：每个批次回显自己固定的规则版本，不跟随规则当前版本变化。"""
    items = service.list_batches(rule_id=rule_id, status=status, audit_status=audit_status)
    return {"items": items, "total": len(items)}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int) -> dict[str, Any]:
    """批次详情：返回固定规则版本、规则快照、原始记录与历史清洗值，供批次抽屉回显。"""
    batch = service.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"清洗批次 {batch_id} 不存在或已归档")
    return batch


@router.post("/batches")
def start_batch(payload: EntryPayload) -> ActionResult:
    """基于启用中的规则启动新批次；停用规则会被拒绝，批次启动即固定规则版本与快照。"""
    rule_id_raw = payload.values.get("rule_id")
    try:
        rule_id = int(rule_id_raw)
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="缺少有效的 rule_id，无法启动清洗批次")
    batch, message = service.start_batch(rule_id)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.post("/batches/{batch_id}/submit")
def submit_batch(batch_id: int) -> ActionResult:
    """提交批次结果：幂等，重复提交不改写历史，始终返回该批次固定版本的清洗结果。"""
    batch, message = service.submit_batch(batch_id)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.post("/batches/{batch_id}/interrupt")
def interrupt_batch(batch_id: int) -> ActionResult:
    """规则切换中断：批次保留固定版本与已清洗数据，等待从原批次续做。"""
    batch, message = service.interrupt_batch(batch_id)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.post("/batches/{batch_id}/resume")
def resume_batch(batch_id: int) -> ActionResult:
    """从原批次续做：不新建批次、不换规则版本。"""
    batch, message = service.resume_batch(batch_id)
    if batch is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=batch)


@router.get("/audit")
def list_audit(
    audit_status: str | None = Query(default=None, description="默认返回全部，传 待处理 只看待处理区"),
) -> dict[str, Any]:
    """清洗审计待处理区：暂存停用规则时仍在处理中的批次记录，处理优先级待定。"""
    items = service.list_audit(audit_status=audit_status)
    return {"items": items, "total": len(items)}


@router.get("/dataset")
def get_dataset() -> dict[str, Any]:
    """按现有取数逻辑返回清洗数据集，并给出更新节点定位信息。"""
    return service.get_dataset_node()


@router.post("/dataset/restore")
def restore_dataset() -> ActionResult:
    """恢复清洗数据集到初始基线；历史批次固定版本与清洗值保持不变。"""
    node, message = service.restore_dataset()
    return ActionResult(ok=True, message=message, entry=node)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据清洗清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "cleanse", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条清洗规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"清洗规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条清洗规则，全字段落库，缺必填字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="清洗规则已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """保存规则编辑：保留未提交字段并递增规则版本，历史批次仍固定旧版本。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条清洗规则执行提交校验、启用规则、停用规则；停用只阻止新批次并暂存冲突批次。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
