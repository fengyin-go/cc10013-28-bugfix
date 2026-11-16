"""数据清洗规则版本固定逻辑的端到端复测脚本（不依赖网络与数据库）。

覆盖需求：
- 停用前已启动批次固定旧版本；停用只阻止新批次；
- 停用后新批次固定新版本；历史清洗值不被覆盖；
- 重复提交/重复切换幂等，不改写历史；
- 中断后续做沿用原批次原版本；
- 清洗审计待处理区暂存停用冲突；批次详情回显规则版本；
- 数据集恢复后历史结果仍对得上；每个批次只有一个规则版本。
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/workspace/backend")

from app.services.cleanse import CleanseService
from app.store import store

service = CleanseService()
PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def reset() -> None:
    """每个场景用独立内存仓库，避免相互污染。"""
    store._tables.clear()
    from app.seed import SEED_ROWS
    for name, rows in SEED_ROWS.items():
        store._tables[name] = [dict(row) for row in rows]


print("== 场景1：启动即固定版本，规则再编辑也不污染已启动批次 ==")
reset()
rule = service.get_entry(3)
check("种子规则3为已启用", rule["status"] == "已启用", rule["status"])
check("种子规则版本为 v1", rule["规则版本"] == "v1", rule["规则版本"])

b1, msg = service.start_batch(3)
check("停用前可启动批次", b1 is not None, msg)
check("批次固定 v1", b1["规则版本"] == "v1", b1["规则版本"])
check("批次快照版本为 v1", b1["规则快照"]["规则版本"] == "v1")
v1_results = [dict(r) for r in b1["清洗结果"]]
v1_metrics = dict(b1["指标"])
check("种子数据去重剔除1条", v1_metrics["去重剔除数"] == 1, v1_metrics)
check("种子数据异常剔除1条", v1_metrics["异常值剔除数"] == 1, v1_metrics)
check("种子数据空值替换1条", v1_metrics["空值替换数"] == 1, v1_metrics)

# 编辑规则：空值替换与去重键变化，版本升到 v2
upd, umsg = service.update_entry(3, {"空值替换": "匿名", "去重键": "手机号"})
check("规则保存成功", upd is not None, umsg)
check("规则版本递增到 v2", upd["规则版本"] == "v2", upd["规则版本"])
check("未提交字段保留（目标字段仍在）", upd["目标字段"] == "手机号", upd["目标字段"])
check("已启动批次版本仍固定 v1", service.get_batch(b1["id"])["规则版本"] == "v1")
check("已启动批次清洗结果未被改写", service.get_batch(b1["id"])["清洗结果"] == v1_results)

print("== 场景2：停用只阻止新批次，处理中批次进审计待处理区 ==")
rule_off, dmsg = service._disable_rule(service.get_entry(3)) if False else service.run_action(3, "停用规则")
check("停用成功", rule_off["status"] == "已停用", dmsg)
blocked, bmsg = service.start_batch(3)
check("停用后新批次被阻止", blocked is None, bmsg)
check("阻止原因可读", "停用" in bmsg, bmsg)
audit = service.list_audit()
check("审计待处理区有1条", len(audit) == 1, audit)
check("审计记录为待处理", audit and audit[0]["审计状态"] == "待处理", audit)
check("审计记录固定旧版本 v1", audit and audit[0]["规则版本"] == "v1", audit)
check("审计优先级待定", audit and audit[0]["处理优先级"] == "待定", audit)
# 重复停用幂等，不重复造审计记录
service.run_action(3, "停用规则")
check("重复停用不重复生成审计记录", len(service.list_audit()) == 1, service.list_audit())
check("历史批次清洗值依旧未变", service.get_batch(b1["id"])["清洗结果"] == v1_results)

print("== 场景3：重新启用后新批次固定新版本，历史批次保留旧版本 ==")
service.run_action(3, "启用规则")
check("重新启用后版本保持 v2", service.get_entry(3)["规则版本"] == "v2")
b2, msg2 = service.start_batch(3)
check("启用后可再启动新批次", b2 is not None, msg2)
check("新批次固定 v2", b2["规则版本"] == "v2", b2["规则版本"])
check("新批次快照是 v2", b2["规则快照"]["规则版本"] == "v2")
check("旧批次仍是 v1", service.get_batch(b1["id"])["规则版本"] == "v1")
v2_results = [dict(r) for r in b2["清洗结果"]]
# 新版本去重键改成手机号，空值替换改成匿名；两版结果应可区分
v1_blank_filled = any(r.get("手机号") == "未登记" for r in v1_results)
v2_blank_filled = any(r.get("手机号") == "匿名" for r in v2_results)
check("旧版空值替换为 未登记", v1_blank_filled)
check("新版空值替换为 匿名", v2_blank_filled)
check("每个批次只有一个规则版本", b1["规则版本"] != b2["规则版本"])

print("== 场景4：重复提交幂等，不改写历史 ==")
done1, _ = service.submit_batch(b1["id"])
check("旧批次提交完成", done1["状态"] == "已完成", done1["状态"])
check("完成批次版本仍 v1", done1["规则版本"] == "v1")
again, amsg = service.submit_batch(b1["id"])
check("重复提交被接受但不改写", again["清洗结果"] == v1_results, amsg)
check("重复提交指标不变", again["指标"] == v1_metrics)

print("== 场景5：规则切换中断后从原批次续做 ==")
interrupted, imsg = service.interrupt_batch(b2["id"])
check("新批次可中断", interrupted["状态"] == "已中断", imsg)
check("中断批次固定版本仍 v2", interrupted["规则版本"] == "v2")
resumed, rmsg = service.resume_batch(b2["id"])
check("可从原批次续做", resumed["状态"] == "运行中", rmsg)
check("续做不换版本仍 v2", resumed["规则版本"] == "v2")
check("续做不换批次编号", resumed["批次编号"] == b2["批次编号"])
done2, _ = service.submit_batch(b2["id"])
check("续做后提交完成", done2["状态"] == "已完成")
check("完成批次结果与启动时一致", done2["清洗结果"] == v2_results)

print("== 场景6：批次详情抽屉回显固定版本与历史值 ==")
detail = service.get_batch(b1["id"])
check("详情含规则版本", detail["规则版本"] == "v1")
check("详情含规则快照", detail["规则快照"]["去重键"] == "用户ID")
check("详情含原始记录", len(detail["原始记录"]) == 6)
check("详情含清洗结果", len(detail["清洗结果"]) == v1_metrics["输出行数"])

print("== 场景7：恢复清洗数据集后历史结果仍对得上 ==")
# 先污染数据集
dataset = store.rows("cleanse_dataset")
dataset.append({"id": 99, "用户ID": "UX", "手机号": "13900000099", "城市": "测试"})
node, nmsg = service.restore_dataset()
check("恢复接口成功", node is not None, nmsg)
check("数据集恢复为6行", node["total"] == 6, node["total"])
check("更新节点含取数入口", "/api/cleanse/dataset" in node["取数入口"])
check("恢复后历史批次1结果不变", service.get_batch(b1["id"])["清洗结果"] == v1_results)
check("恢复后历史批次2结果不变", service.get_batch(b2["id"])["清洗结果"] == v2_results)

print("== 场景8：保存时字段不丢失（去重键/格式/空值/版本/生效日期） ==")
created, cmsg = service.create_entry({"规则编号": "CLEA-NEW", "目标字段": "城市", "空值替换": "未知"})
check("登记成功", created is not None, cmsg)
for field in ["去重键", "格式规范化", "异常值剔除", "规则版本", "生效日期"]:
    check(f"字段 {field} 有落库值", field in created and created[field] is not None, created)
check("默认版本 v1", created["规则版本"] == "v1")
bad, missing = service.create_entry({"规则编号": "CLEA-BAD"})
check("缺必填字段被拦", bad is None and "目标字段" in missing, missing)

print()
print(f"总计：{PASS} 通过，{FAIL} 失败")
raise SystemExit(1 if FAIL else 0)
