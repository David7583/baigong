# ============================================================
# 文件名: release_preflight_v0001.py
# 中文名: v0018 发布候选隔离检查与人工样本验收
# 版本号: v0001
#
# 主层级: action
# 层级: tests / release
# 脚本定位: 对本任务候选执行静态、导入、错误及 mock/离线真实 embedding 主链测试
#
# 职责说明:
# - 以新 run_id 保存源码哈希及实际测试结果
#
# 本脚本做什么:
# - 建立人工样本与隔离数据库，检查首次、重复、缺少授权及失败注入
#
# 本脚本不做什么:
# - 仅显式 --model-path 才调用本地模型；不安装依赖、不写正式库、不发布、不删除旧证据
#
# 制度边界声明:
# - 所有产物只进入本任务 temp/evidence，已有 run_id 拒绝覆盖
# - 子进程禁网、保留中间产物；结果准确报告，不把 mock 当作真实验收
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: release_preflight_v0001
# family: release_preflight
# role: isolated_release_verifier
# version: v0001
# status: experimental
# entry_point: tests/release_preflight_v0001.py
# input:
#   - source_manifest and candidate scripts
# output:
#   - per-run JSON evidence and isolated artificial databases
# depends_on:
#   - Python stdlib
#   - PyYAML
#   - existing DuckDB and Chroma for mock integration
# used_by: []
# ============================================================

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

import yaml


DEFAULT_ENCODING = "utf-8"
SCRIPT_FAMILY = "release_preflight"
SCRIPT_NAME = "release_preflight_v0001.py"
SCRIPT_VERSION = "v0001"
ROOT = Path(__file__).resolve().parents[1]
# Windows extended-length spelling accesses the same fixed task directory.
if os.name == "nt" and not str(ROOT).startswith("\\\\?\\"):
    ROOT = Path("\\\\?\\" + str(ROOT))
ENTRY = ROOT / "scripts/orchestration/action/data_action_chain_pipeline_v0018.py"


# ============================================================
# 异常类型与工具函数区
# ============================================================

class VerificationError(RuntimeError):
    pass


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding=DEFAULT_ENCODING)


def synthetic_conversation():
    texts = [
        "This artificial release sample describes a laboratory inventory. Each sample has a stable identifier, an original source and a processing record. No real personal information is included.",
        "The laboratory inventory keeps the original source unchanged. Derived records retain stable identifiers and links to evidence. Repeated processing must not create duplicate facts.",
        "Please compare the artificial source records with derived records. The laboratory inventory requires stable identifiers, timestamps and a recoverable processing history for every sample.",
        "The synthetic verification checks stable identifiers and unchanged original sources. It also checks that repeated processing is safe, failures are explicit and evidence remains available.",
    ]
    nodes = {}
    for i, text in enumerate(texts):
        key = f"node-{i}"
        nodes[key] = {"id": key, "parent": f"node-{i-1}" if i else None,
                      "children": [f"node-{i+1}"] if i < 3 else [],
                      "message": {"id": f"message-{i}", "author": {"role": "user" if i % 2 == 0 else "assistant", "name": None, "metadata": {}},
                                  "create_time": 1700000000.0 + i, "update_time": None,
                                  "content": {"content_type": "text", "parts": [text]},
                                  "status": "finished_successfully", "metadata": {}, "recipient": "all", "weight": 1}}
    return [{"id": "synthetic-release-001", "conversation_id": "synthetic-release-001",
             "title": "Artificial release fixture", "create_time": 1700000000.0,
             "update_time": 1700000003.0, "current_node": "node-3", "mapping": nodes}]


def snapshot(path):
    if not path.exists():
        return None
    # Only caller-created artificial databases are inspected here.
    with closing(sqlite3.connect(str(path))) as connection:
        names = [x[0] for x in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {"integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
                "rows": {name: connection.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in names},
                "content_sha256": {name: hashlib.sha256("\n".join(sorted(repr(row) for row in connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"'))).encode()).hexdigest() for name in names}}


# ============================================================
# 核心验收组件
# ============================================================

class Verification:
    def __init__(self, run_id, model_path=None):
        self.model_path = Path(model_path).resolve() if model_path else None
        if self.model_path and not (self.model_path / "modules.json").is_file():
            raise VerificationError("local_model_missing")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise VerificationError("invalid_run_id")
        self.run_id = run_id
        self.base = ROOT / "temp" / run_id
        self.evidence = ROOT / "evidence" / (run_id + ".json")
        if self.base.exists() or self.evidence.exists():
            raise VerificationError("run_already_exists")
        self.base.mkdir(parents=True)
        self.env = {k: v for k, v in os.environ.items() if not re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.I)}
        self.env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
                        HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", ANONYMIZED_TELEMETRY="False",
                        HF_HUB_DISABLE_TELEMETRY="1", RELEASE_TEST_ROOT=str(ROOT),
                        PYTHONPATH=str(ROOT / "tests/runtime_guard"))
        for key in ["TEMP", "TMP", "HF_HOME", "XDG_CACHE_HOME"]:
            p = self.base / key.lower()
            p.mkdir()
            self.env[key] = str(p)
        self.results = []
        self.test_hash = digest(Path(__file__))
        self.guard_hash = digest(ROOT / "tests/runtime_guard/sitecustomize.py")
        self.manifest_hash = digest(ROOT / "provenance/source_manifest.json")

    def command(self, name, args, input_text="", timeout=180):
        started = time.perf_counter()
        try:
            proc = subprocess.run([sys.executable, "-B", *map(str, args)], cwd=ROOT, env=self.env,
                                  input=input_text, capture_output=True, text=True, encoding=DEFAULT_ENCODING,
                                  timeout=timeout)
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            code, out, err = -1, str(exc.stdout or ""), "timeout"
        (self.base / (name + ".stdout.txt")).write_text(out, encoding=DEFAULT_ENCODING)
        (self.base / (name + ".stderr.txt")).write_text(err, encoding=DEFAULT_ENCODING)
        record = {"name": name, "returncode": code, "seconds": round(time.perf_counter()-started, 3),
                  "argv": [str(x).replace(str(ROOT), "<TASK_ROOT>") for x in args]}
        return record, out, err

    def check(self, record, success, **details):
        record.update(passed=bool(success), **details)
        self.results.append(record)
        self.persist()
        print(json.dumps({"test": record["name"], "passed": bool(success)}), flush=True)

    def persist(self):
        save(self.evidence, {"run_id": self.run_id, "updated_at": datetime.now(timezone.utc).isoformat(),
                            "python_version": sys.version, "mode": "offline_bge_m3" if self.model_path else "offline_mock_only",
                            "test_sha256": self.test_hash, "guard_sha256": self.guard_hash,
                            "source_manifest_sha256": self.manifest_hash,
                            "results": self.results, "passed": all(r["passed"] for r in self.results)})

    def static(self):
        manifest = json.loads((ROOT / "provenance/source_manifest.json").read_text(encoding=DEFAULT_ENCODING))
        failures = [r["path"] for r in manifest["files"] if digest(ROOT/r["path"]) != r["candidate_sha256"]]
        scripts = sorted((ROOT / "scripts").rglob("*.py"))
        syntax_errors = []
        for p in scripts:
            try:
                ast.parse(p.read_text(encoding="utf-8-sig"))
            except SyntaxError:
                syntax_errors.append(p.relative_to(ROOT).as_posix())
        self.check({"name": "source_hash_and_syntax"}, not failures and not syntax_errors,
                   files=len(manifest["files"]), scripts=len(scripts), hash_failures=failures, syntax_errors=syntax_errors)
        probe = "import socket; socket.create_connection(('127.0.0.1',9),timeout=1)"
        r, out, err = self.command("network_guard", ["-c", probe])
        self.check(r, r["returncode"] != 0 and "release_test_network_disabled" in err)
        probe = "from pathlib import Path; Path('..','release_guard_forbidden.tmp').write_text('must be blocked')"
        r, out, err = self.command("write_guard", ["-c", probe])
        self.check(r, r["returncode"] != 0 and "release_test_write_outside_task" in err)
        def load(p):
            code = "import importlib.util,sys; from pathlib import Path; p=Path(sys.argv[1]); sys.path.insert(0,str(p.parent)); s=importlib.util.spec_from_file_location('release_check_module',p); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m)"
            return self.command("import_"+p.stem, ["-c", code, p])
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(load, scripts))
        self.check({"name": "isolated_imports"}, all(r[0]["returncode"] == 0 for r in rows),
                   count=len(rows), failures=[r[0] for r in rows if r[0]["returncode"] != 0])
        r, out, err = self.command("entry_help", [ENTRY, "--help"])
        self.check(r, r["returncode"] == 0 and "--retention-mode" in out)
        replay = ROOT / "scripts/action/anchor/replay_ingest_run_v0004.py"
        text = replay.read_text(encoding="utf-8-sig")
        self.check({"name": "upstream_replay_guard_preserved"}, "shell=False" in text and "--confirm-external-command" in text)

    def smoke(self):
        data = self.base / "data/data_raw/sample.json"
        save(data, synthetic_conversation())
        source_hash = digest(data)
        store = self.base / "s"
        store.mkdir()
        cfg = yaml.safe_load((ROOT / "config/action/config/sql_writer_config_v0001.yml").read_text(encoding=DEFAULT_ENCODING))
        cfg["connection"]["sqlite"]["path"] = (store / "action_data.db").relative_to(ROOT).as_posix()
        writer = store / "writer.yml"
        writer.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding=DEFAULT_ENCODING)
        embed = yaml.safe_load((ROOT / "config/action/config/embedding_generator_config_v0002.yml").read_text(encoding=DEFAULT_ENCODING))
        embed["backend"]["device"] = "cpu"
        embed["runtime"]["batch_size"] = 4
        if self.model_path:
            embed["backend"]["model_name"] = str(self.model_path)
        embedding = store / "embed.yml"
        embedding.write_text(yaml.safe_dump(embed, allow_unicode=True), encoding=DEFAULT_ENCODING)
        args = [ENTRY, "--project-root", ROOT, "--data-root", data.parents[1], "--target", "data_raw/sample.json",
                "--asset-version", "v0001", "--data-db", store/"data.db", "--data-table", "data_text_units",
                "--action-db", store/"action_data.db", "--sql-writer-config", writer,
                "--duckdb-path", store/"derived.duckdb", "--semantic-path", "/conversations/*/nodes/*/message/content/parts/*/text",
                "--semantic-limit", "64", "--test-mode", "--confirm-execution", "--confirm-database-write",
                "--mock-api", "--retention-mode", "keep", "--embed-config", embedding, "--step-timeout", "600"]
        if self.model_path:
            args.remove("--mock-api")
            args.append("--confirm-model-api")
        paths = {"--output-root":"o", "--admission-target-root":"admitted", "--registry-dir":"registry",
                 "--structural-business-root":"structural", "--anchor-business-root":"anchor", "--derivation-business-root":"derivation",
                 "--return-root":"returns", "--vector-pipeline-root":"vectors", "--chromadb-path":"chroma",
                 "--vector-state-index":"state.jsonl", "--vector-replaced-archive":"replaced",
                 "--data-intermediate-root":"intermediate", "--data-workspace-root":"workspace"}
        for flag, folder in paths.items():
            args += [flag, store/folder]
        r, out, err = self.command("initialize_artificial_action_db", [
            ROOT/"scripts/action/tools/init_action_data_sql_schema_v0001.py", "--config",
            ROOT/"config/action/init_schema/action_data_sql_schema_config_v0001.yml",
            "--db-path", store/"action_data.db", "--init"])
        self.check(r, r["returncode"] == 0)
        if r["returncode"]:
            return
        baseline = snapshot(store/"action_data.db")
        r, out, err = self.command("dry_run", args+["--run-id", "dry", "--dry-run"])
        self.check(r, r["returncode"] == 0 and snapshot(store/"action_data.db") == baseline)
        denied = [a for a in args if a != "--confirm-execution"]
        r, out, err = self.command("missing_confirmation", denied+["--run-id", "denied"], "keep\nyes\n")
        self.check(r, r["returncode"] != 0 and "confirm-execution" in out+err and snapshot(store/"action_data.db") == baseline)
        if self.model_path:
            probe = """import sys,json,importlib.util,numpy as np,chromadb
from pathlib import Path
p=Path(sys.argv[1]); spec=importlib.util.spec_from_file_location('embedding_reference',p)
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
cfg={'backend':{'model_name':sys.argv[2],'device':'cpu','encode_batch_size':2}}
docs=['苹果和香蕉是水果。','汽车发动机需要定期保养。','数据库备份可以帮助恢复数据。']
queries=['哪些食物属于水果？','如何保养汽车的发动机？','怎样通过备份恢复数据库？']
v=np.asarray(m._local_model_call(docs+queries,cfg)); again=np.asarray(m._local_model_call([docs[0]],cfg))
assert v.shape==(6,1024) and np.isfinite(v).all()
assert np.allclose(np.linalg.norm(v,axis=1),1,atol=1e-4)
assert np.allclose(v[0],again[0],atol=1e-5)
Path(sys.argv[3]).mkdir(parents=True,exist_ok=True)
c=chromadb.PersistentClient(path=sys.argv[3]).create_collection('semantic_check',metadata={'hnsw:space':'cosine'})
c.add(ids=['fruit','car','database'],documents=docs,embeddings=v[:3].tolist())
r=c.query(query_embeddings=v[3:].tolist(),n_results=1)
assert [x[0] for x in r['ids']]==['fruit','car','database']
print(json.dumps({'shape':list(v.shape),'norms':np.linalg.norm(v,axis=1).tolist(),'top1':r['ids'],'distances':r['distances'],'repeat_max_abs_error':float(np.max(np.abs(v[0]-again[0])))}))
"""
            r, out, err = self.command("bge_m3_semantic_retrieval", ["-c", probe,
                ROOT/"scripts/action/vector/embedding_generator_v0006.py", self.model_path, store/"semantic_chroma"], timeout=600)
            self.check(r, r["returncode"] == 0, detail=out[-3000:], error=err[-1500:] if r["returncode"] else "")
        previous = None
        previous_derived = None
        for label in ["first", "repeat"]:
            version = "v0001" if label == "first" else "v0002"
            r, out, err = self.command(label, args+["--run-id", label, "--asset-version", version], "keep\nyes\n", timeout=600)
            try:
                result = json.loads(out)
            except ValueError:
                result = {}
            ok = r["returncode"] == 0 and result.get("status") == "completed"
            state = {name: snapshot(store/name) for name in ["data.db", "action_data.db"]}
            derived = None
            if ok:
                probe = ("import chromadb,duckdb,json,hashlib,sys; "
                         "c=chromadb.PersistentClient(path=sys.argv[1]).get_collection('action_data_embeddings'); "
                         "v=c.get(include=['embeddings']); "
                         "pairs=sorted(zip(v['ids'],v['embeddings'].tolist())); "
                         "d=duckdb.connect(sys.argv[2],read_only=True); "
                         "tables=[r[0] for r in d.execute('SHOW TABLES').fetchall()]; "
                         "print(json.dumps({'vectors':c.count(),'vector_hash':hashlib.sha256(json.dumps(pairs,sort_keys=True).encode()).hexdigest(),"
                         "'duckdb':{t:d.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in tables}}))")
                dr, dout, derr = self.command(label+"_derived_probe", ["-c", probe, store/"chroma", store/"derived.duckdb"])
                try:
                    derived = json.loads(dout)
                except ValueError:
                    pass
                self.check(dr, dr["returncode"] == 0 and bool(derived) and derived["vectors"] > 0, derived=derived)
            if label == "repeat" and previous:
                tables = ["concept_units", "instance_units", "unit_attributes"]
                ok = ok and all(state["action_data.db"]["rows"].get(k) == previous["action_data.db"]["rows"].get(k) for k in tables)
                ok = ok and derived == previous_derived
                if result.get("completion_manifest"):
                    completion = json.loads(Path(result["completion_manifest"]).read_text(encoding=DEFAULT_ENCODING))
                    ok = ok and completion.get("no_new_content") is True
            ok = ok and all(v and v["integrity"] == "ok" for v in state.values())
            self.check(r, ok, result_status=result.get("status"), snapshots=state)
            if not ok:
                break
            previous = state
            previous_derived = derived
        if previous and all(x["passed"] for x in self.results):
            bad = data.with_name("bad.json")
            bad.write_text("{invalid", encoding=DEFAULT_ENCODING)
            r, out, err = self.command("malformed_input", args+["--run-id", "bad", "--target", "data_raw/bad.json", "--asset-version", "v0003"], "keep\nyes\n", timeout=180)
            after = {name: snapshot(store/name) for name in ["data.db", "action_data.db"]}
            bad_failure_path = store/"o/data_action_chain_pipeline/v0018/bad/failure.json"
            bad_failure = json.loads(bad_failure_path.read_text(encoding=DEFAULT_ENCODING)) if bad_failure_path.exists() else {}
            self.check(r, r["returncode"] != 0 and after == previous and bad_failure.get("transaction", {}).get("state") == "ROLLED_BACK")
            fault = synthetic_conversation()
            fault[0]["mapping"]["node-0"]["message"]["content"]["parts"][0] += " Additional artificial fact for rollback verification."
            save(data.with_name("fault.json"), fault)
            r, out, err = self.command("injected_anchor_failure", args+["--run-id", "fault", "--target", "data_raw/fault.json", "--asset-version", "v0003", "--test-fail-after-step", "action_anchor_persistence"], "keep\nyes\n", timeout=600)
            after = {name: snapshot(store/name) for name in ["data.db", "action_data.db"]}
            failure_manifest = store/"o/data_action_chain_pipeline/v0018/fault/failure.json"
            failure = json.loads(failure_manifest.read_text(encoding=DEFAULT_ENCODING)) if failure_manifest.exists() else {}
            self.check(r, r["returncode"] != 0 and after == previous and failure.get("status") == "failed"
                       and failure.get("transaction", {}).get("state") == "ROLLED_BACK"
                       and "action_anchor_persistence" in failure.get("completed_steps", []), rollback_snapshots=after)
        self.check({"name": "artificial_source_unchanged"}, digest(data) == source_hash, sha256=source_hash)


# ============================================================
# CLI / main 接口区
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model-path", help="Explicit local BGE-M3 directory; offline real inference")
    parser.add_argument("--mode", choices=["static", "smoke", "all"], default="all")
    args = parser.parse_args()
    try:
        check = Verification(args.run_id, args.model_path)
        if args.mode in {"static", "all"}:
            check.static()
        if args.mode in {"smoke", "all"}:
            check.smoke()
        passed = all(x["passed"] for x in check.results)
        print(json.dumps({"status": "passed" if passed else "failed", "evidence": str(check.evidence)}))
        return 0 if passed else 2
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "detail": str(exc)}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
