"""3.0：只核三代理登记、证据和版本，不证明真实调用、阅读或文笔质量。"""
from pathlib import Path
import argparse
import hashlib
import json

ROLES = {"主笔", "文笔编辑", "设定审校"}
DONE = "本轮内部复核完成"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(data, record_path, candidate_sha256=None, require_complete=False):
    base = Path(record_path).resolve().parent
    errors = []

    def need(ok, message):
        if not ok:
            errors.append(message)

    def obj(value):
        return value if isinstance(value, dict) else {}

    def rows(value, label):
        need(isinstance(value, list) and all(isinstance(x, dict) for x in value), label + "须为对象列表")
        return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []

    def text(value):
        return isinstance(value, str) and bool(value.strip()) and "待填" not in value

    def strings(value):
        return isinstance(value, list) and bool(value) and all(text(x) for x in value)

    def file(name, expected, label):
        path = (base / name).resolve() if text(name) else base
        actual = digest(path) if path.is_file() else None
        need(bool(actual) and isinstance(expected, str) and expected.lower() == actual,
             label + "文件缺失或哈希不符")
        return path, actual

    def evidence(value, label):
        value = obj(value)
        return file(value.get("file"), value.get("sha256"), label)

    need(isinstance(data, dict), "记录须为对象")
    data = obj(data)
    need(data.get("schema_version") == "prose-workflow-3.0" and data.get("workflow_version") == "3.0", "3.0记录版本不符")
    need(data.get("mode") == "真实子代理", "3.0仅接受真实子代理登记")
    need(not any(k in data for k in ("delivery_flow", "optional_roles", "collaboration_record")), "3.0不接受旧六阶段或附加岗位字段")
    status = data.get("status")
    need(status in {"候选未审", "返修中", "部分完成_有未决", "停止", DONE}, "任务状态不合法")
    scenes = data.get("scene_ids")
    need(strings(scenes), "缺有效场景范围")
    scenes = scenes if strings(scenes) else []
    need(len(set(scenes)) == len(scenes), "场景范围重复")
    candidate, actual = file(data.get("candidate_file"), data.get("candidate_sha256"), "当前候选")
    if candidate_sha256 is not None:
        need(actual == candidate_sha256.lower(), "调用方候选版本不一致")

    assignments = rows(data.get("assignments"), "派工")
    roles = [x.get("role") for x in assignments]
    need(len(assignments) == 3 and all(isinstance(x, str) for x in roles) and set(x for x in roles if isinstance(x, str)) == ROLES,
         "须恰有主笔、文笔编辑、设定审校三个岗位")
    agents = [x.get("agent_id") for x in assignments]
    need(all(text(x) and x not in {"/root", "root", "总控"} for x in agents)
         and len(set(x for x in agents if isinstance(x, str))) == 3, "须三个不同真实代理ID，不能由总控或占位符兼任")
    owners = {x.get("role"): x.get("agent_id") for x in assignments if isinstance(x.get("role"), str)}
    for row in assignments:
        label = str(row.get("role")) + "："
        need(row.get("transport") == "subagent", label + "须使用subagent")
        need(row.get("status") in {"进行中", "已完成"}, label + "须有实际派工状态")
        scope = row.get("scene_ids")
        need(strings(scope) and set(scope) == set(scenes), label + "未覆盖当前场景")
        entries = rows(row.get("dispatch_evidence"), label + "派工证据")
        need(bool(entries), label + "缺派工证据")
        for entry in entries:
            path, _ = evidence(entry, label)
            if path.is_file():
                content = path.read_text("utf-8-sig")
                need(text(row.get("agent_id")) and row["agent_id"] in content, label + "派工证据未包含登记ID")

    reports = set()

    def report(gate, expected_hash, reviewer, label, scope_required=True):
        path, report_hash = file(gate.get("report_file"), gate.get("report_sha256"), label + "报告")
        need(path != candidate and report_hash != actual, label + "报告与正文须分开")
        need(path not in reports, label + "须使用独立报告")
        reports.add(path)
        need(gate.get("reviewer") == reviewer and text(reviewer), label + "审核人不符")
        need(gate.get("candidate_sha256") == expected_hash and bool(expected_hash), label + "报告候选版本过期")
        if not path.is_file():
            return
        try:
            header = obj(json.loads(path.read_text("utf-8-sig")))
        except (ValueError, UnicodeError):
            need(False, label + "报告须为可读JSON报告头")
            return
        need(header.get("status") == gate.get("status"), label + "报告实际结论与登记不一致")
        need(header.get("candidate_sha256") == expected_hash and header.get("reviewer") == reviewer,
             label + "报告头版本或审核人不符")
        need(text(header.get("scope")), label + "报告头缺实际读取/继承范围")
        if scope_required:
            scope = header.get("scene_ids")
            need(strings(scope) and set(scope) == set(scenes), label + "报告未覆盖当前场景")

    gates = obj(data.get("gates"))
    need(set(gates) == {"prose", "content"}, "须独立文笔、内容两项结论")
    for name, role in (("prose", "文笔编辑"), ("content", "设定审校")):
        gate = obj(gates.get(name))
        need(gate.get("status") in {"待审", "通过", "未通过", "过期"}, name + "状态不合法")
        if gate.get("status") != "待审":
            report(gate, actual, owners.get(role), name)

    issues = rows(data.get("issues"), "问题")
    issue_ids = [x.get("id") for x in issues]
    need(all(text(x) for x in issue_ids) and len(set(x for x in issue_ids if isinstance(x, str))) == len(issues), "问题ID缺失或重复")
    pending = []
    for row in issues:
        need(text(row.get("problem")) and text(row.get("location")), "问题缺具体缺陷或位置")
        need(type(row.get("required")) is bool, "问题须标明是否必修")
        need(row.get("status") in {"待处理", "已解决", "保留有据", "待裁决"}, "问题状态不合法")
        if row.get("required") and row.get("status") not in {"已解决", "保留有据"}:
            pending.append(row.get("id"))
        if row.get("status") in {"已解决", "保留有据"}:
            need(row.get("candidate_sha256") == actual and text(row.get("decision")), "问题关闭缺当前版本或具体依据")

    attempts = rows(data.get("attempts"), "实际处理")
    need(1 <= len(attempts) <= 3, "实际处理须为1至3次，片段重写计入同一上限")
    rewrite_count = 0
    document_paths = set()
    for index, row in enumerate(attempts):
        need(type(row.get("number")) is int and row.get("number") == index + 1, "实际处理编号须连续")
        need(row.get("kind") in {"首稿", "定向重写", "修订"}, "实际处理类型不合法")
        need(index == 0 or row.get("kind") != "首稿", "不能将后续处理重新标为首稿")
        refs = row.get("issue_ids")
        need(isinstance(refs, list) and all(isinstance(x, str) and x in issue_ids for x in refs), "实际处理引用问题不合法")
        if row.get("kind") != "首稿":
            need(strings(refs), "重写或修订须有具体问题")
        need(type(row.get("sample_rewrite")) is bool, "缺片段重写标记")
        rewrite_count += row.get("sample_rewrite") is True
        if row.get("sample_rewrite"):
            need(row.get("kind") == "定向重写", "片段重写须记作定向重写")
        out_path, out_hash = file(row.get("candidate_file"), row.get("candidate_sha256"), "处理产物")
        doc_path, doc_hash = file(row.get("document_file"), row.get("document_sha256"), "处理说明")
        need(doc_path not in {candidate, out_path} and doc_hash not in {actual, out_hash}, "处理说明不能用正文代替")
        need(doc_path not in document_paths, "处理说明须分别保存")
        document_paths.add(doc_path)
    if attempts:
        need(attempts[-1].get("candidate_sha256") == actual, "最终处理未绑定当前候选")

    sample = obj(data.get("sample_gate"))
    need(type(sample.get("required")) is bool and text(sample.get("reason")), "片段门槛须记录启用决定及依据")
    need(type(sample.get("rewrite_count")) is int and sample.get("rewrite_count") in {0, 1}
         and sample.get("rewrite_count") == rewrite_count, "片段只能定向重写一次且须计入实际处理")
    sample_status = sample.get("status")
    sample_ok = False
    if sample.get("required") is False:
        need(sample_status == "不适用" and rewrite_count == 0, "未启用片段门槛不能登记片段处理")
        sample_ok = sample_status == "不适用"
    elif sample.get("required") is True:
        need(sample_status in {"待审", "通过", "未通过", "继承通过"}, "片段状态不合法")
        if sample_status != "待审":
            sample_path, sample_hash = file(sample.get("file"), sample.get("sha256"), "片段")
            report(sample, sample_hash, owners.get("文笔编辑"), "片段", scope_required=False)
            need(sample_path not in reports, "片段不能兼作报告")
            if sample_status == "继承通过":
                evidence(sample.get("inheritance_evidence"), "片段继承依据")
            sample_ok = sample_status in {"通过", "继承通过"}
        if sample_status == "未通过" and rewrite_count == 1:
            need(status == "停止", "同类片段定向重写仍失败须停止")
    closed = (sample_ok and not pending and all(obj(gates.get(x)).get("status") == "通过" for x in ("prose", "content"))
              and all(x.get("status") == "已完成" for x in assignments))
    if status == DONE or require_complete:
        need(status == DONE and closed, "不能完成：片段、独立审查或必修问题尚未闭合")
    complete = not errors and closed and status == DONE
    return {"valid": not errors, "complete": complete, "structurally_valid": not errors,
            "declared_complete_and_closed": complete, "errors": errors,
            "candidate_sha256": actual, "pending_issue_ids": pending, "assignments": assignments,
            "three_agent_flow_checked": True,
            "scope": "仅核结构、身份登记、证据文件与哈希；真实调用、阅读和质量须核工具记录及正文"}


def validate_record(path, require_complete=False, candidate_sha256=None):
    path = Path(path).resolve()
    try:
        return validate(json.loads(path.read_text("utf-8-sig")), path, candidate_sha256, require_complete)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"valid": False, "complete": False, "structurally_valid": False,
                "declared_complete_and_closed": False, "errors": ["记录不可核验：" + str(exc)]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    parser.add_argument("--complete", "--require-complete", action="store_true", dest="complete")
    args = parser.parse_args()
    result = validate_record(args.record, args.complete)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["valid"] and (not args.complete or result["complete"]) else 1)
