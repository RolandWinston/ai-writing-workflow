"""Local, read-only auditing and fail-closed candidate checks. Python standard library."""
import argparse
import ast
import hashlib
import json
import re
import sqlite3
import uuid
from collections import Counter
from pathlib import Path


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def count(value):
    return len(re.findall(r"[\u4e00-\u9fff]", value))


def inspect_scripts(paths):
    findings = []
    for path in paths:
        source = Path(path).read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # Trace .get(..., default) expressions; an old revised default survives
            # even when the missing key is never represented in a patch file.
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and len(node.args) > 1:
                default = node.args[1]
                if any(isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and n.slice.value == "revised" for n in ast.walk(default)):
                    findings.append({"kind": "OLD_REVISED_DEFAULT", "file": str(path), "line": node.lineno})
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "已在完整场景连读" in node.value:
                findings.append({"kind": "GENERATED_READING_CLAIM", "file": str(path), "line": node.lineno})
    return findings


def risks(rows, ratio=0.55, minimum=30):
    output = []
    for row in rows:
        for old, new in (("raw", "base"), ("base", "revised"), ("revised", "final"), ("prior_revised", "final")):
            if old not in row or new not in row:
                continue
            before, after = row[old] or "", row[new] or ""
            if before == after or not before.strip():
                continue
            n, m = count(before), count(after)
            tags = []
            if not after.strip():
                tags.append("EMPTY")
            elif n >= minimum and m / n < ratio:
                tags.append("LARGE_REDUCTION")
            reason = row.get("merge_reason", "")
            if new == "final" and any(x in reason for x in ("淡出", "中性", "亲密")):
                tags.append("FADE_SCENE_REVIEW")
            if not tags:
                continue
            output.append({"key": f"{row['id']}:{old}>{new}", "id": row["id"], "stage": f"{old}>{new}", "tags": tags,
                           "before_han": n, "after_han": m, "before_sha256": text_hash(before), "after_sha256": text_hash(after),
                           "chapter": row.get("chapter"), "part": row.get("part"), "reading_line": row.get("reading_line"),
                           "semantic_review": "待逐项核对独有心理、关系回应、对白、动作后果和接头；字数只用于排查"})
    return output


def check_dispositions(items, decisions, rows):
    """No semantic verdict inferred from a reason label. Decisions bind exact texts."""
    by = {r["id"]: r for r in rows}
    indexed = {d["key"]: d for d in decisions}
    errors = []
    for item in items:
        d = indexed.get(item["key"], {})
        if not d:
            errors.append({"key": item["key"], "error": "MISSING_DISPOSITION"})
            continue
        required = ("reviewer_agent", "scene_ids", "lost_or_retained_information", "decision", "before_sha256", "after_sha256", "evidence_file", "evidence_sha256")
        if any(not d.get(k) for k in required):
            errors.append({"key": item["key"], "error": "INCOMPLETE_DISPOSITION"})
        if any(d.get(k) != item[k] for k in ("before_sha256", "after_sha256")):
            errors.append({"key": item["key"], "error": "STALE_DISPOSITION"})
        if d.get("decision") not in ("保留有据", "已解决", "可选"):
            errors.append({"key": item["key"], "error": "OPEN_DECISION"})
        if d.get("disposition") in ("合并", "重复删除"):
            targets = d.get("destination_ids", [])
            if not targets or any(not by.get(i, {}).get("final", "").strip() for i in targets):
                errors.append({"key": item["key"], "error": "MISSING_LIVE_DESTINATION"})
        elif not d.get("removal_basis"):
            errors.append({"key": item["key"], "error": "MISSING_REMOVAL_BASIS"})
        evidence = Path(d.get("evidence_file", ""))
        if not evidence.is_file() or sha(evidence) != d.get("evidence_sha256"):
            errors.append({"key": item["key"], "error": "INVALID_DISPOSITION_EVIDENCE"})
    return errors


def initialize(rows, approvals, mapping_sha):
    """Use base only after all raw->base transformations have explicit provenance."""
    if approvals.get("mapping_sha256") != mapping_sha:
        raise ValueError("BASELINE_MAPPING_HASH_REQUIRED")
    by = {a["id"]: a for a in approvals.get("transformations", [])}
    result = []
    for row in rows:
        if "raw" not in row or "base" not in row:
            raise ValueError("MISSING_RAW_OR_BASE:" + row["id"])
        if row["raw"] != row["base"]:
            approval = by.get(row["id"], {})
            if not all(approval.get(k) for k in ("authority", "reason", "evidence_file", "evidence_sha256")):
                raise ValueError("UNVERIFIED_BASE_TRANSFORMATION:" + row["id"])
            if approval.get("base_sha256") != text_hash(row["base"]):
                raise ValueError("STALE_BASE_APPROVAL:" + row["id"])
            evidence = Path(approval["evidence_file"])
            if not evidence.is_file() or sha(evidence) != approval["evidence_sha256"]:
                raise ValueError("INVALID_BASE_EVIDENCE:" + row["id"])
        result.append({"id": row["id"], "final": row["base"], "baseline_sha256": text_hash(row["base"]), "reason": "待场景审读", "reading_status": "未登记"})
    return result


ROLES = {"文笔作家", "编辑", "逻辑家", "天道意志", "多样化审查"}


def publication_check(manifest):
    if manifest.get("workflow_version") == "3.0":
        from 核验三代理流程 import validate_record
        # 3.0只接受任务记录引用，不解释旧五岗签名或六阶段门禁。
        if any(key in manifest for key in ("packages", "optional_roles", "source_chain_gate", "role_gate", "task_gate", "reading_gate")):
            return ["V3_REJECTS_LEGACY_MANIFEST_FIELDS"]
        record = Path(manifest.get("record_file", ""))
        if not record.is_file() or sha(record) != manifest.get("record_sha256"):
            return ["V3_RECORD_MISSING_OR_HASH_MISMATCH"]
        data = load(record)
        if data.get("workflow_version") != "3.0":
            return ["V3_RECORD_VERSION_REQUIRED"]
        result = validate_record(record, require_complete=True, candidate_sha256=manifest.get("candidate_sha256"))
        errors = list(result["errors"])
        candidate = Path(manifest.get("candidate", ""))
        if not candidate.is_file() or sha(candidate) != manifest.get("candidate_sha256") or sha(candidate) != result.get("candidate_sha256"):
            errors.append("CANDIDATE_MISSING_OR_HASH_MISMATCH")
        return errors
    if manifest.get("workflow_version") not in (None, "2.1", "2.2"):
        return ["UNKNOWN_WORKFLOW_VERSION"]
    errors = []
    optional = set(manifest.get("optional_roles", []))
    if optional - {"网文导师", "错别字代理"}:
        errors.append("UNKNOWN_OPTIONAL_ROLE")
    required_roles = ROLES | optional
    candidate = Path(manifest.get("candidate", ""))
    digest = sha(candidate) if candidate.is_file() else None
    if not digest or digest != manifest.get("candidate_sha256"):
        errors.append("CANDIDATE_MISSING_OR_HASH_MISMATCH")
    files = manifest.get("files", [])
    required = {"阅读稿", "对照稿", "来源映射", "风险去向", "任务记录", "派工记录", "检查点"}
    if required - {f.get("kind") for f in files}:
        errors.append("REQUIRED_ARTIFACTS_MISSING")
    for file in files:
        path = Path(file["path"])
        if not path.is_file() or sha(path) != file.get("sha256"):
            errors.append("ARTIFACT_MISSING_OR_HASH_MISMATCH:" + str(path))
    packages = manifest.get("packages", [])
    if not packages:
        errors.append("NO_PACKAGE_COVERAGE")
    for package in packages:
        signatures = package.get("signatures", [])
        rolemap = {s.get("role"): s for s in signatures}
        if required_roles - set(rolemap):
            errors.append("ROLE_SIGNATURE_MISSING:" + package["id"])
        ids = [s.get("agent_id") for s in signatures]
        if None in ids or "" in ids or len(set(ids)) != len(ids):
            errors.append("ROLE_IDENTITY_COLLISION:" + package["id"])
        for sig in signatures:
            report = Path(sig.get("report", ""))
            if sig.get("candidate_sha256") != digest or sig.get("status") != "完成" or not report.is_file() or sha(report) != sig.get("report_sha256"):
                errors.append("INVALID_SIGNATURE:" + package["id"] + ":" + str(sig.get("role")))
            if set(sig.get("scene_ids", [])) != set(package.get("scene_ids", [])) or not package.get("scene_ids"):
                errors.append("INCOMPLETE_SIGNATURE_SCOPE:" + package["id"])
    # These must be actual outputs from separately executed gates, on this hash.
    for name in ("source_chain_gate", "role_gate", "task_gate", "reading_gate"):
        gate = manifest.get(name, {})
        path = Path(gate.get("file", ""))
        if not path.is_file() or sha(path) != gate.get("sha256"):
            errors.append("MISSING_GATE_EVIDENCE:" + name)
            continue
        actual = load(path)
        if actual.get("candidate_sha256") != digest or actual.get("passed") is not True:
            errors.append("GATE_NOT_PASSED:" + name)
    return errors


def audit_real(project, output):
    project, output = Path(project), Path(output)
    mapping = project / "交付/04_逐段来源与改稿.json"
    rows = load(mapping)
    items = risks(rows)
    script_findings = inspect_scripts([project / "修订沙漠后半.py", project / "候选/生成沙漠前半阶段.py"])
    expected = ["交付/07_逻辑与复核记录.md", "交付/08_交付核验.json", "审查/最终复核索引.json", "交付冻结.json"]
    missing = [p for p in expected if not (project / p).is_file()]
    final_reports = list((project / "审查/最终复核").glob("*.md"))
    missing_signatures = [prefix for prefix in ("L01", "T01", "D01", "E01", "M01") if not any(p.name.startswith(prefix + "_") for p in final_reports)]
    summary = {"mapping_sha256": sha(mapping), "rows": len(rows),
               "new_empty_prior_to_final": sum(bool(r.get("prior_revised", "").strip()) and not r.get("final", "").strip() for r in rows),
               "empty_final": sum(not r.get("final", "").strip() for r in rows),
               "risks": len(items), "risk_stages": dict(Counter(i["stage"] for i in items)),
               "script_findings": script_findings, "missing_artifacts": missing, "missing_final_report_prefixes": missing_signatures,
               "disposition_errors": len(check_dispositions(items, [], rows)), "passed": False,
               "scope": "机械发现；未判定每处删改的文学质量，未修改旧稿"}
    save(output / "全链风险清单.json", items)
    save(output / "真实故障核验.json", summary)
    return summary


def reading_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=3)
    db.execute("CREATE TABLE IF NOT EXISTS readings (source TEXT, reader TEXT, paragraph TEXT, digest TEXT, token TEXT, status TEXT, note TEXT, PRIMARY KEY(source,reader,paragraph,digest))")
    db.execute("BEGIN IMMEDIATE")
    return db


def record_reading(receipt, note):
    """Store the agent's bounded result, not a machine claim of comprehension."""
    if not note.strip() or len(note) > 600:
        raise ValueError("NOTE_REQUIRED_MAX_600: cite facts, issue IDs or existing report location")
    record = load(receipt)
    db = reading_db(record["reading_index"])
    try:
        current = db.execute("SELECT COUNT(*) FROM readings WHERE token=?", (record["read_token"],)).fetchone()[0]
        if current != len(record["indexed_ids"]):
            raise ValueError("READ_RECEIPT_SUPERSEDED: do not close a newer reading with an old receipt")
        db.execute("UPDATE readings SET status='recorded', note=? WHERE token=?", (note.strip(), record["read_token"]))
        record['reading_note'] = note.strip()
        save(receipt, record)
        db.commit()
    finally:
        db.close()


def invalidate_reading(index, source_key, ids, reason):
    if not ids or not reason.strip():
        raise ValueError('FEEDBACK_SCOPE_AND_REASON_REQUIRED')
    db = reading_db(index)
    try:
        for paragraph in ids:
            db.execute("UPDATE readings SET status='invalid', note=?, token=? WHERE source=? AND paragraph=?",
                       (reason, uuid.uuid4().hex, source_key, paragraph))
        db.commit()
    finally:
        db.close()


def read_slice(mapping, start, end, stage, max_chars, receipt, *, reading_index=None,
               reader=None, source_key=None, changed_only=False, reread_reason=None):
    """Emission receipt is not proof of human/agent reading."""
    rows = load(mapping)
    ids = [r["id"] for r in rows]
    a, b = ids.index(start), ids.index(end)
    if b < a:
        raise ValueError("RANGE_REVERSED")
    selected = rows[a:b + 1]
    db = None
    if reading_index:
        if not reader or not source_key:
            raise ValueError("STABLE_READER_AND_SOURCE_REQUIRED")
        if reread_reason and not (8 <= len(reread_reason.strip()) <= 400):
            raise ValueError("REREAD_REASON_REQUIRED: identify the new question or lost context")
        if Path(receipt).exists():
            raise ValueError("RECEIPT_EXISTS: preserve the previous reading record")
        db = reading_db(reading_index)
        try:
            if Path(receipt).exists():
                raise ValueError("RECEIPT_EXISTS")
            hits = [db.execute("SELECT status,note FROM readings WHERE source=? AND reader=? AND paragraph=? AND digest=? AND status!='invalid'",
                               (source_key, reader, r['id'], text_hash(r.get(stage, '')))).fetchone() for r in selected]
            if all(hits) and not reread_reason:
                notes = list(dict.fromkeys(hit[1] or '上次已输出但未登记结论；先补记录或说明恢复上下文的必要范围' for hit in hits))
                return json.dumps({'action': 'REUSE', 'ids': [r['id'] for r in selected], 'notes': notes,
                                   'scope': '同岗位相同文字；不代表其他岗位验收或历史删段已查清'}, ensure_ascii=False)
            indexed_ids = {r['id'] for r, hit in zip(selected, hits) if not hit or reread_reason}
            if any(hits) and not reread_reason:
                if not changed_only:
                    raise ValueError("PARTLY_ALREADY_READ: use --changed-only or give a specific --reread-reason")
                fresh = {i for i, hit in enumerate(hits) if not hit}
                keep = fresh | {j for i in fresh for j in (i - 1, i + 1) if 0 <= j < len(selected)}
                selected = [r for i, r in enumerate(selected) if i in keep]
            body = '\n\n'.join(r['id'] + '\n' + r.get(stage, '') for r in selected)
            if len(body) > max_chars:
                raise ValueError(f"OUTPUT_LIMIT:{len(body)}>{max_chars}; split at a scene boundary")
            token = uuid.uuid4().hex
            record = {"mapping_sha256": sha(mapping), "stage": stage, "ids": [r['id'] for r in selected],
                      "emitted_chars": len(body), "payload_sha256": text_hash(body),
                      "status": "已输出，待代理提交实读记录", "reading_index": str(Path(reading_index).resolve()),
                      "reader": reader, "source_key": source_key, "read_token": token, "reread_reason": reread_reason,
                      "indexed_ids": [r['id'] for r in selected if r['id'] in indexed_ids]}
            for r in selected:
                if r['id'] in indexed_ids:
                    db.execute("INSERT OR REPLACE INTO readings VALUES (?,?,?,?,?,'pending','')",
                               (source_key, reader, r['id'], text_hash(r.get(stage, '')), token))
            save(receipt, record)
            db.commit()
            return body
        finally:
            db.close()
    body = "\n\n".join(r["id"] + "\n" + r.get(stage, "") for r in selected)
    if len(body) > max_chars:
        raise ValueError(f"OUTPUT_LIMIT:{len(body)}>{max_chars}; split at a scene boundary")
    save(receipt, {"mapping_sha256": sha(mapping), "stage": stage, "ids": [r["id"] for r in selected], "emitted_chars": len(body), "payload_sha256": text_hash(body), "status": "已输出，待代理提交实读记录"})
    return body


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit-real")
    audit.add_argument("project")
    audit.add_argument("output")
    read = sub.add_parser("read")
    read.add_argument("mapping")
    read.add_argument("start")
    read.add_argument("end")
    read.add_argument("--stage", choices=["raw", "base", "revised", "final"], default="base")
    read.add_argument("--max-chars", type=int, default=8000)
    read.add_argument("--receipt", required=True)
    read.add_argument("--reading-index", required=True)
    read.add_argument("--reader", required=True, help="Stable role identity, retained across agent restarts")
    read.add_argument("--source-key", required=True, help="Stable book/source identity, retained across versions")
    read.add_argument("--changed-only", action="store_true")
    read.add_argument("--reread-reason")
    note = sub.add_parser("read-note")
    note.add_argument("receipt")
    note.add_argument("note", help="Bounded result or pointer to an existing issue/report; max 600 characters")
    invalid = sub.add_parser('read-invalidate')
    invalid.add_argument('index')
    invalid.add_argument('source_key')
    invalid.add_argument('reason')
    invalid.add_argument('ids', nargs='+')
    init = sub.add_parser("init")
    init.add_argument("mapping")
    init.add_argument("approvals")
    init.add_argument("output")
    publish = sub.add_parser("publish-check")
    publish.add_argument("manifest")
    args = parser.parse_args()
    if args.command == "audit-real":
        result = audit_real(args.project, args.output)
        print(json.dumps(result, ensure_ascii=True))
        return 1 if not result["passed"] else 0
    if args.command == "read":
        print(read_slice(args.mapping, args.start, args.end, args.stage, args.max_chars, args.receipt,
                         reading_index=args.reading_index, reader=args.reader, source_key=args.source_key,
                         changed_only=args.changed_only, reread_reason=args.reread_reason))
    if args.command == "read-note":
        record_reading(args.receipt, args.note)
        print("阅读结果已登记；这不自动代表内容或文笔通过。")
    if args.command == 'read-invalidate':
        invalidate_reading(args.index, args.source_key, args.ids, args.reason)
        print('该范围旧阅读结论已失效，后续各岗位必须重新核对。')
    if args.command == "init":
        if Path(args.output).exists():
            raise ValueError("CANDIDATE_ALREADY_EXISTS: choose a new version path")
        save(args.output, initialize(load(args.mapping), load(args.approvals), sha(args.mapping)))
    if args.command == "publish-check":
        errors = publication_check(load(args.manifest))
        print(json.dumps({"passed": not errors, "errors": errors}, ensure_ascii=True))
        return 1 if errors else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
