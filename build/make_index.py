#!/usr/bin/env python3
"""生成/更新仓库根目录的 fnpack.json（给飞牛 FnDepot 集合源用的索引片段）。

读取本次构建产物与其 Release 资产信息，产出 schema_version=2 的索引。
每次 CI 发完 Release 都会跑一次并提交，保证版本 / sha256 / 体积与实物一致。
"""
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_NAME = "Hermes"


def sh(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def read_manifest(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def sha256_of(path: str) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def release_asset(repo: str, tag: str, name: str) -> tuple[str, int] | None:
    """从 GitHub Release 取资产的 sha256 与体积（避免依赖本地包）。"""
    import urllib.request

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "make-index")
    if token:
        req.add_header("Authorization", f"token {token}")
    with urllib.request.urlopen(req, timeout=120) as r:
        rel = json.loads(r.read().decode())
    for a in rel.get("assets", []):
        if a.get("name") != name:
            continue
        digest = (a.get("digest") or "").replace("sha256:", "")
        if digest and a.get("size"):
            return digest, int(a["size"])
    return None


def main() -> int:
    fpk = sys.argv[1] if len(sys.argv) > 1 else None
    if not fpk:
        cands = [f for f in os.listdir(ROOT) if f.endswith(".fpk")]
        if not cands:
            print("找不到 .fpk，跳过索引生成")
            return 0
        fpk = cands[0]
    fpk = os.path.basename(fpk)
    fpk_path = os.path.join(ROOT, fpk)

    manifest = read_manifest(os.path.join(ROOT, "manifest"))
    # 以已发布的 tag 为准：本地 manifest 可能被上一次本地打包改过，不能当权威
    env_tag = os.environ.get("RELEASE_TAG", "").strip()
    version = env_tag.lstrip("vV") if env_tag else manifest.get("version", "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+(-[a-z0-9]+)?(-\d+)?", version or ""):
        print(f"manifest 版本异常: {version!r}")
        return 1

    repo = os.environ.get("GITHUB_REPOSITORY", "aaiuh/hermes-studio-fnos")
    tag = os.environ.get("RELEASE_TAG", f"v{version}")

    # 优先信任 GitHub 上已发布资产的实测值；本地包只做兜底（本地包可能未发布）
    remote = release_asset(repo, tag, fpk)
    if remote:
        digest, size = remote
        src = f"Release {tag} 的实测值"
    elif os.path.exists(fpk_path):
        digest, size = sha256_of(fpk_path)
        src = f"本地文件 {fpk}"
    else:
        print(f"既取不到 Release {tag} 的 {fpk}，也没有本地文件，跳过")
        return 0
    download_url = f"https://github.com/{repo}/releases/download/{tag}/{fpk}"

    webui = open(os.path.join(ROOT, "VERSION"), encoding="utf-8").read().strip()
    agent = open(os.path.join(ROOT, "AGENT_VERSION"), encoding="utf-8").read().strip()

    idx_path = os.path.join(ROOT, "fnpack.json")
    old = {}
    if os.path.exists(idx_path):
        try:
            old = json.load(open(idx_path, encoding="utf-8"))
        except json.JSONDecodeError:
            old = {}

    releases = (old.get("apps", {}).get(APP_NAME, {}) or {}).get("releases", {}) or {}
    releases[version] = {
        "changelog": (
            f"跟随官方 runtime（Agent {agent}，控制台 {webui}）。"
            "内嵌官方 Agent 运行时，空白机器全新安装即可用；"
            "控制台可在 Studio 内直接更新；支持飞牛应用数据备份与还原。"
        ),
        "updated_at": __import__("datetime").date.today().isoformat(),
        "os_min_version": "1.1.0",
        "packages": {
            "x86": {
                "download_url": download_url,
                "sha256": digest,
                "size": size,
            }
        },
    }

    # 剔除指向已删除 Release 的残留条目：URL 取不到就不该留在索引里，
    # 否则 FnDepot 会把它当成可安装版本。
    alive = {}
    for v, r in releases.items():
        url = (((r or {}).get("packages") or {}).get("x86") or {}).get("download_url", "")
        if v == version:
            alive[v] = r
            continue
        ok = False
        if url:
            try:
                import urllib.request

                req = urllib.request.Request(url, method="HEAD")
                with urllib.request.urlopen(req, timeout=20) as resp:
                    ok = 200 <= resp.status < 400
            except Exception:
                ok = False
        if ok:
            alive[v] = r
        else:
            print(f"  丢弃无效版本 {v}（下载地址不可达）")
    releases = alive

    # 只保留最近 15 个版本，避免索引无限膨胀
    keep = sorted(releases.keys(), key=_ver_key)[-15:]
    releases = {k: releases[k] for k in keep}

    data = {
        "schema_version": "2",
        "source_info": {
            "name": "Hermes Studio 飞牛源",
            "author": "aaiuh",
            "homepage": f"https://github.com/{repo}",
            "description": "Hermes Studio 的飞牛 fnOS 原生封装：内嵌官方 Agent 运行时，支持全新安装、fpk 内更新与飞牛应用数据备份。",
        },
        "apps": {
            APP_NAME: {
                "display_name": "Hermes Studio",
                "desc": (
                    "Hermes Agent 的图形控制台与运行时，原生运行于飞牛 fnOS，不依赖 Docker。"
                    "内嵌官方 Agent 运行时（全新安装即可用），控制台可在 Studio 内直接更新，"
                    "支持飞牛应用备份与恢复。依赖系统应用 Node.js 24（不随包携带）。"
                ),
                "platform": ["x86"],
                "categories": ["AI赋能"],
                "icon_url": f"https://raw.githubusercontent.com/{repo}/main/ICON_256.PNG",
                "preview_urls": [],
                # FnDepot 取 readme 时要求「纯文本 md」地址；给仓库 HTML 页会拿到代理站的落地页
                "readme_url": f"https://raw.githubusercontent.com/{repo}/main/README.md",
                "bug_report_url": f"https://github.com/{repo}/issues",
                "maintainer": "aaiuh",
                "maintainer_url": "https://github.com/aaiuh",
                "distributor": "aaiuh",
                "distributor_url": f"https://github.com/{repo}",
                "run_as": "package",
                "install_type": "",
                "is_docker": False,
                "service_port": "8648",
                "releases": releases,
            }
        },
    }

    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"fnpack.json 已更新：{APP_NAME} {version}  {size} B  sha256={digest[:16]}…  来源：{src}")
    return 0


def _ver_key(v: str):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)(?:-[a-zA-Z0-9]+)?(?:-(\d+))?$", v)
    if not m:
        return (0, 0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0))


if __name__ == "__main__":
    raise SystemExit(main())
