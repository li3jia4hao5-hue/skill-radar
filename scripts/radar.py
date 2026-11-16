"""Skill Radar v0.1: bounded local evidence, public GitHub discovery and checked reports.

Python 3.10+, standard library only. Does not install or execute candidate skills.
Semantic assessment belongs to the host agent; numeric scores are judgments, not probabilities.
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = "0.1.0"
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "data", "db", "assets", "uploads", "logs", "log", "cache", "build", "dist", ".next", "vendor", "work", "outputs", "output", "tmp", "temp", ".openai", ".aws"}
DOCS = {"readme.md", "product.md", "architecture.md", "agents.md", "skill.md", "requirements.md", "prd.md"}
SOURCE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".html", ".css", ".go", ".rs", ".java"}
WEIGHTS = {"fit": 40, "compatibility": 25, "incremental": 15, "maintenance": 10, "popularity": 10}
SECRET_LINE = re.compile(r"(?i)(?:password|passwd|api[ _-]?key|access[ _-]?token|secret|密码|口令|密钥)\s*[:=：]")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8-sig"))


def write_json(p, value):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def redact(text):
    text = re.sub(r"(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED PRIVATE KEY]", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{16,})\b", "[REDACTED]", text)
    return "\n".join("[REDACTED credential line]" if SECRET_LINE.search(line) else line for line in text.splitlines())


def bounded_text(p, limit=32000):
    with p.open("rb") as f:
        raw = f.read(limit + 1)
    return redact(raw[:limit].decode("utf-8-sig", errors="replace")), len(raw) > limit


def metadata_value(header, key):
    match = re.search(r"(?m)^" + re.escape(key) + r":\s*([^\n]*)", header)
    if not match:
        return ""
    value = match.group(1).strip()
    if value in {"|", ">", "|-", ">-", "|+", ">+"}:
        remaining = header[match.end():].lstrip("\r\n")
        block = []
        for line in remaining.splitlines():
            if line and not line[0].isspace():
                break
            block.append(line.strip())
        return " ".join(block).strip()
    return value.strip(' \"\'')


def within(p, root):
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def local_inventory(root):
    result = []
    for folder in (root / ".agents/skills", root / ".claude/skills", root / ".codex/skills"):
        if folder.exists():
            result += skill_inventory(folder)
    return result


def skill_inventory(root):
    """Read only names/descriptions from an explicitly selected installed skill root."""
    result = []
    if not root.is_dir():
        return result
    for p in sorted(root.iterdir()):
        if p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()):
            result.append({"name": p.name, "path": str(p), "description": "", "status": "linked-unread"})
            continue
        f = p / "SKILL.md"
        if f.is_file():
            text, _ = bounded_text(f, 6000)
            header = text.split("---", 2)[1] if text.startswith("---") and text.count("---") >= 2 else ""
            result.append({"name": metadata_value(header, "name") or p.name,
                           "path": str(f), "description": metadata_value(header, "description"),
                           "status": "metadata-read"})
    return result


def scan_project(spec):
    root = Path(spec["path"]).resolve()
    if not root.is_dir():
        raise ValueError(f"Project directory missing: {root}")
    files, errors = [], []
    truncated = False
    for directory, dirs, names in os.walk(root, followlinks=False):
        current = Path(directory)
        depth = len(current.relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP and not d.startswith('.')
                         and not (current / d).is_symlink()
                         and not (hasattr(current / d, "is_junction") and (current / d).is_junction())) if depth < 4 else []
        for name in sorted(names):
            p = current / name
            if p.is_symlink() or not within(p, root):
                continue
            # Never collect arbitrary JSON/config, database, media or environment files.
            if p.suffix.lower() in SOURCE_EXT or name.lower() in DOCS or name == "package.json" or name == "pyproject.toml" or re.fullmatch(r"requirements[\w.-]*\.txt", name):
                files.append(p)
            if len(files) >= 2500:
                truncated = True
                break
        if truncated:
            break
    evidence = []
    # Root documents and explicitly selected docs; no automatic business-data traversal.
    docs = [p for p in files if p.parent == root and p.name.lower() in DOCS]
    for rel in spec.get("documents", []):
        p = root / rel
        if not within(p, root) or p.suffix.lower() not in {".md", ".txt"}:
            raise ValueError(f"Invalid document selection: {rel}")
        if any(x.lower() in SKIP or x.startswith('.') for x in p.relative_to(root).parts):
            raise ValueError(f"Excluded document location: {rel}")
        if p.is_file() and p not in docs:
            docs.append(p)
    for p in docs[:12]:
        try:
            text, cut = bounded_text(p)
            evidence.append({"path": p.relative_to(root).as_posix(), "text": text, "truncated": cut,
                             "sha256": hashlib.sha256(text.encode()).hexdigest()})
        except OSError as e:
            errors.append({"path": p.name, "error": type(e).__name__})
    manifests = []
    for p in files:
        rel = p.relative_to(root).as_posix()
        if p.name == "package.json" and len(p.relative_to(root).parts) <= 3:
            try:
                if p.stat().st_size > 200000:
                    continue
                data = read_json(p)
                deps = {}
                for field in ("dependencies", "devDependencies", "peerDependencies"):
                    for name, version in data.get(field, {}).items():
                        version = str(version)
                        deps[name] = version if re.fullmatch(r"[\w.*^~<>=| +\-]+", version) else "[non-registry source omitted]"
                manifests.append({"path": rel, "dependencies": deps, "script_names": sorted(data.get("scripts", {}))})
            except (OSError, ValueError, AttributeError) as e:
                errors.append({"path": rel, "error": type(e).__name__})
        elif (p.name == "pyproject.toml" or re.fullmatch(r"requirements[\w.-]*\.txt", p.name)) and p.parent == root:
            text, cut = bounded_text(p, 16000)
            # URLs may carry credentials; remove them from saved evidence.
            text = re.sub(r"https?://\S+", "[dependency URL omitted]", text)
            manifests.append({"path": rel, "text": text, "truncated": cut})
    return {"id": spec["id"], "name": spec.get("name", spec["id"]), "root": str(root),
            "scope_note": spec.get("scope_note", ""), "evidence": evidence, "manifests": manifests,
            "source_counts": dict(collections.Counter(p.suffix for p in files if p.suffix in SOURCE_EXT)),
            "source_paths": [p.relative_to(root).as_posix() for p in files if p.suffix in SOURCE_EXT][:500],
            "installed_skills": local_inventory(root), "errors": errors,
            "coverage": {"max_depth": 4, "file_limit_reached": truncated, "source_contents_read": False,
                         "warning": "Local documents describe capabilities; they do not prove runtime or production status."}}


def scan(config):
    ids = [p["id"] for p in config["projects"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Project IDs must be unique")
    result = {"schema_version": 1, "version": VERSION, "scanned_at": now(),
              "projects": [scan_project(p) for p in config["projects"]], "global_installed_skills": []}
    for root in config.get("installed_skill_roots", []):
        result["global_installed_skills"] += skill_inventory(Path(root))
    result["global_installed_skills"] += config.get("additional_installed_skills", [])
    return result


def github(endpoint):
    if not endpoint.startswith("/") or endpoint.startswith("//"):
        raise ValueError("Expected GitHub API relative path")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "skill-radar/" + VERSION, "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request("https://api.github.com" + endpoint, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError("GitHub response exceeded 8 MB; narrow the scope")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub HTTP {e.code}; retry only after checking authorization/rate limits. No complete coverage claimed.") from None


def search(plan):
    items, failures, queries = {}, [], []
    for q in plan["queries"][:8]:
        if not isinstance(q, str) or len(q) > 250:
            raise ValueError("Queries must be generic strings of at most 250 characters")
        # Only this explicit plan leaves the machine; scan/profile data is never sent.
        params = urllib.parse.urlencode({"q": q, "per_page": 10, "sort": "stars", "order": "desc"})
        try:
            response = github("/search/repositories?" + params)
            queries.append({"query": q, "total_count": response.get("total_count"), "incomplete_results": response.get("incomplete_results"), "pages_fetched": 1})
            for repo in response["items"]:
                name = repo["full_name"]
                row = items.setdefault(name, {k: repo.get(k) for k in ("full_name", "html_url", "description", "stargazers_count", "archived", "pushed_at", "default_branch", "license")})
                row.setdefault("matched_queries", []).append(q)
        except (RuntimeError, OSError, ValueError) as e:
            failures.append({"query": q, "error": str(e)})
    return {"checked_at": now(), "queries": queries, "repositories": list(items.values()), "failures": failures,
            "coverage": "At most 8 queries, first 10 repositories each. Not exhaustive. Repository stars are not skill stars."}


def inspect_skill(repo, skill_path):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Expected owner/repository")
    if not skill_path.endswith("SKILL.md") or ".." in skill_path.split("/") or skill_path.startswith("/"):
        raise ValueError("Expected a repository-relative SKILL.md path")
    meta = github("/repos/" + repo)
    commit = github("/repos/" + repo + "/commits/" + urllib.parse.quote(meta["default_branch"], safe=""))["sha"]
    data = github("/repos/" + repo + "/contents/" + urllib.parse.quote(skill_path, safe="/") + "?ref=" + commit)
    if data.get("encoding") != "base64" or data.get("size", 0) > 200000:
        raise ValueError("Skill text unavailable or too large; inspect explicitly")
    text = base64.b64decode(data["content"]).decode("utf-8")
    match = re.search(r"(?m)^name:\s*['\"]?([a-z0-9-]+)", text)
    if not text.startswith("---") or not match:
        raise ValueError("Missing skill frontmatter/name")
    return {"id": repo + ":" + skill_path, "name": match.group(1), "repo": repo, "path": skill_path,
            "commit": commit, "source_url": f"https://github.com/{repo}/blob/{commit}/{skill_path}",
            "checked_at": now(), "repo_stars": meta["stargazers_count"], "repo_pushed_at": meta["pushed_at"],
            "repo_archived": meta["archived"], "license": (meta.get("license") or {}).get("spdx_id"),
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text, "verified": True,
            "verification_scope": "Pinned SKILL.md read only. Referenced files and scripts require separate review before use."}


def score(values):
    if set(values) != set(WEIGHTS):
        raise ValueError("Scores must include: " + ", ".join(WEIGHTS))
    for v in values.values():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 5:
            raise ValueError("Each score must be a number from 0 to 5")
    return round(sum(values[k] / 5 * w for k, w in WEIGHTS.items()), 1)


def validate_assessment(profile, candidates, assessment):
    projects = {p["id"]: p for p in profile["projects"]}
    if len(candidates) != len({c["id"] for c in candidates}):
        raise ValueError("Candidate IDs must be unique")
    by_id = {c["id"]: c for c in candidates}
    for c in candidates:
        if c.get("verified"):
            if not re.fullmatch(r"[0-9a-f]{40}", c.get("commit", "")):
                raise ValueError("Verified candidate needs a 40-character commit SHA")
            expected_url = f"https://github.com/{c['repo']}/blob/{c['commit']}/{c['path']}"
            if c.get("source_url") != expected_url:
                raise ValueError("Candidate source URL does not match pinned commit/path")
            if c.get("sha256") != hashlib.sha256(c.get("text", "").encode()).hexdigest():
                raise ValueError("Candidate content hash mismatch")
    seen, output = set(), []
    for row in assessment["decisions"]:
        key = row["project_id"], row["candidate_id"]
        if key in seen:
            raise ValueError("Duplicate project/candidate decision")
        seen.add(key)
        if key[0] not in projects or key[1] not in by_id:
            raise ValueError("Unknown project or candidate")
        c, p = by_id[key[1]], projects[key[0]]
        if row["decision"] not in {"recommend", "conditional", "reuse", "skip"}:
            raise ValueError("Invalid decision")
        if row["compatibility"] not in {"yes", "unknown", "no"}:
            raise ValueError("Invalid compatibility")
        if row["decision"] == "recommend" and (row["compatibility"] != "yes" or row.get("already_covered") or not c.get("verified") or c.get("repo_archived")):
            raise ValueError("Cannot recommend unverified, incompatible, archived or already covered candidate")
        paths = {d["path"] for d in p["evidence"] + p["manifests"]} | set(p["source_paths"])
        if not row.get("project_evidence") or not set(row["project_evidence"]).issubset(paths):
            raise ValueError("Decision needs known local evidence paths")
        if not row.get("skill_evidence") or row["skill_evidence"] not in c.get("text", ""):
            raise ValueError("Skill evidence must be an exact excerpt of retrieved skill text")
        if not row.get("reason") or not row.get("constraints"):
            raise ValueError("Decision needs reason and constraints")
        if set(row.get("score_reasons", {})) != set(WEIGHTS) or any(not str(x).strip() for x in row["score_reasons"].values()):
            raise ValueError("Each score needs an explanation")
        output.append({**row, "score": score(row["scores"]), "candidate": c, "project_name": p["name"]})
    missing = set(projects) - {r["project_id"] for r in output}
    if missing:
        raise ValueError("Every scoped project needs an evaluated candidate or documented skip: " + ", ".join(sorted(missing)))
    return sorted(output, key=lambda r: (-r["score"], r["candidate_id"], r["project_id"]))


def report(profile, candidates, assessment, target):
    rows = validate_assessment(profile, candidates, assessment)
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    labels = {"recommend": "建议试用", "conditional": "有条件候选", "reuse": "复用已有", "skip": "暂不引入"}
    title = assessment.get("title", "Skill Radar · 首次匹配")
    lines = ["# " + title, "", f"生成时间：{now()}；扫描时间：{profile['scanned_at']}", "",
             "分数是宿主 Agent 按公开权重给出的判断，不是成功率。已检查内容不等于已运行验证。Star 是仓库级数据。", "",
             assessment.get("summary", ""), "", "## 覆盖范围", "", assessment.get("coverage", ""), ""]
    cards = []
    h = html.escape
    for p in profile["projects"]:
        lines += ["## " + p["name"], "", p.get("scope_note", ""), ""]
        subset = [r for r in rows if r["project_id"] == p["id"]]
        recommended_count = 0
        for r in subset:
            if r["decision"] == "recommend":
                recommended_count += 1
                if recommended_count > 3:
                    continue
            c = r["candidate"]
            card_title = f"{labels[r['decision']]} · {c['name']} · {r['score']}/100"
            stars = c.get("repo_stars")
            star_text = str(stars) if stars is not None else "未核验"
            lines += ["### " + card_title, "", r["reason"], "",
                      f"- 项目证据：{', '.join(r['project_evidence'])}",
                      f"- 技能证据：{r['skill_evidence']}",
                      f"- 前提与限制：{r['constraints']}",
                      f"- 重复检查：{r.get('overlap', '未记录')}",
                      f"- [查看固定版本原文]({c['source_url']})；仓库 Star：{star_text}；检查：{c['checked_at']}",
                      "- 评分依据：" + "；".join(f"{k} {r['scores'][k]}/5：{r['score_reasons'][k]}" for k in WEIGHTS), ""]
            url = c['source_url']
            if not url.startswith("https://github.com/"):
                raise ValueError("Report source URL must be an HTTPS GitHub link")
            cards.append(f'<article data-status="{h(r["decision"])}"><div class="meta">{h(p["name"])} · {h(labels[r["decision"]])}</div><h2>{h(c["name"])} <small>{r["score"]}</small></h2><p>{h(r["reason"])}</p><p class="muted">证据：{h(", ".join(r["project_evidence"]))}</p><p><b>前提：</b>{h(r["constraints"])}</p><p><b>重复检查：</b>{h(r.get("overlap", "未记录"))}</p><a href="{h(url, quote=True)}" target="_blank" rel="noopener noreferrer">查看已核验版本 ↗</a><span class="stars">仓库 ★ {h(star_text)}</span></article>')
    (target / "report.md").write_text("\n".join(lines), encoding="utf-8")
    doc = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + h(title) + '</title><style>body{margin:0;background:#f4f6f3;color:#182c28;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1120px;margin:auto;padding:44px 24px}h1{font-size:36px;margin:8px 0}h2{font-size:21px;margin:8px 0}small{float:right;color:#407a68}.lead{max-width:900px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:18px}article{background:white;border:1px solid #dde5df;border-radius:14px;padding:24px}article[data-status=recommend]{border-top:4px solid #28725e}.meta,.muted{font-size:13px;color:#65736c}.stars{float:right;font-size:13px;color:#65736c}a{color:#226b59}footer{margin:32px 0;color:#65736c;font-size:13px}</style><main><div class="meta">SKILL RADAR / FIRST MATCH / v0.1</div><h1>' + h(title) + '</h1><p class="lead">' + h(assessment.get("summary", "")) + '</p><p class="muted">' + h(assessment.get("coverage", "")) + '</p><div class="grid">' + ''.join(cards) + '</div><footer>评分是匹配判断，不是成功概率。未安装候选技能、未运行候选脚本、未修改业务项目。详细评分依据见 report.md。生成于 ' + h(now()) + '</footer></main></html>'
    (target / "report.html").write_text(doc, encoding="utf-8")
    write_json(target / "ranked.json", [{k: v for k, v in r.items() if k != "candidate"} for r in rows])
    return {"evaluated_pairs": len(rows), "projects": len(profile["projects"]), "report": str(target / "report.html")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("scan"); p.add_argument("--config", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("search"); p.add_argument("--plan", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("inspect"); p.add_argument("--repo", required=True); p.add_argument("--path", required=True); p.add_argument("--out", required=True)
    p = sub.add_parser("report"); p.add_argument("--profile", required=True); p.add_argument("--candidates", required=True); p.add_argument("--assessment", required=True); p.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.command == "scan":
        result = scan(read_json(args.config)); write_json(args.out, result)
        print(json.dumps({"projects": len(result["projects"]), "out": args.out}))
    elif args.command == "search":
        result = search(read_json(args.plan)); write_json(args.out, result)
        print(json.dumps({"repositories": len(result["repositories"]), "failures": len(result["failures"]), "out": args.out}))
    elif args.command == "inspect":
        write_json(args.out, inspect_skill(args.repo, args.path)); print(args.out)
    else:
        print(json.dumps(report(read_json(args.profile), read_json(args.candidates), read_json(args.assessment), args.out), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        print(f"Skill Radar stopped: {exc}", file=sys.stderr)
        sys.exit(1)
