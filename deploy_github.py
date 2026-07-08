#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把站点 + 归档推送到 GitHub Pages 仓库（Oracledeer/daily-tech-brief）。

推送内容：
  1) 站点根产物：index.html + style.css（GitHub Pages 直接服务，地址固定）
  2) 归档镜像：把用户自定义本地位置 W:\\DailyNews\\ 下的窗口快照
     同步到仓库的 archives/ 目录，从而手机/任意设备可在线回看历史：
     https://oracledeer.github.io/daily-tech-brief/archives/(START~END.DailyNews)/index.html

凭证策略（安全第一）：
  - 优先读环境变量 GITHUB_TOKEN；用完即清远程 URL，绝不写入项目文件或记忆。
  - 若未提供 GITHUB_TOKEN，则依赖全局 git credential store（~/.git-credentials，
    位于用户主目录、项目目录之外，是标准 git 机制），实现每日自动化免 token 推送。

用法：
  GITHUB_TOKEN=xxx python deploy_github.py      # 一次性带 token 推送
  python deploy_github.py                        # 依赖全局 credential（已配置后可免 token）
"""
import os
import shutil
import subprocess
from pathlib import Path

BASE = Path(r"C:\Users\17210\WorkBuddy\automation-2026-06-24-09-25-21")
REPO = "Oracledeer/daily-tech-brief"
REMOTE_PLAIN = f"https://github.com/{REPO}.git"
PAGES_URL = f"https://oracledeer.github.io/{REPO.split('/')[-1]}/"

SITE_FILES = ["index.html", "style.css", ".gitignore"]
LOCAL_ARCHIVE = Path(r"W:\DailyNews")          # 用户自定义本地归档位置
REPO_ARCHIVES = BASE / "archives"             # 仓库内镜像目录


def run(cmd, check=True):
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=BASE, check=check)


def mirror_archives():
    """把 W:\\DailyNews 下的窗口目录 + .window.json 镜像到仓库 archives/。"""
    if not LOCAL_ARCHIVE.exists():
        print(f"  ℹ️  本地归档位置 {LOCAL_ARCHIVE} 不存在，跳过镜像")
        return
    REPO_ARCHIVES.mkdir(parents=True, exist_ok=True)
    for item in LOCAL_ARCHIVE.iterdir():
        target = REPO_ARCHIVES / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    print(f"  🔁 已镜像归档 → {REPO_ARCHIVES}（来自 {LOCAL_ARCHIVE}）")


def main():
    token = os.environ.get("GITHUB_TOKEN")
    # 防止凭证失败时 git 卡在交互式密码提示（会无限挂起）；失败立即报错
    os.environ["GIT_TERMINAL_PROMPT"] = "0"

    # 1. 初始化仓库（若不存在）
    if not (BASE / ".git").exists():
        run(["git", "init", "-q"])
        run(["git", "config", "user.name", "WorkBuddy"])
        run(["git", "config", "user.email", "workbuddy@local"])
        run(["git", "remote", "add", "origin", REMOTE_PLAIN])

    # 2. 若带 token，临时把远程 URL 注入 token（推完即还原，不落盘）
    if token:
        run(["git", "remote", "set-url", "origin", f"https://{token}@github.com/{REPO}.git"])

    # 3. 暂存站点产物
    for f in SITE_FILES:
        if (BASE / f).exists():
            run(["git", "add", f])

    # 4. 镜像并暂存归档
    mirror_archives()
    if REPO_ARCHIVES.exists():
        run(["git", "add", "archives/"])

    # 5. 提交（无变化则跳过）
    commit = subprocess.run(
        ["git", "commit", "-q", "-m", "deploy: site + window archives"],
        cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if commit.returncode != 0:
        out = (commit.stdout or b"") + (commit.stderr or b"")
        if b"nothing to commit" in out:
            print("  ℹ️  无变化，跳过提交")
        else:
            print("  ⚠️  提交失败：\n", out.decode(errors="ignore"))
            if token:
                run(["git", "remote", "set-url", "origin", REMOTE_PLAIN])
            return

    # 6. 推送（本仓库仅由本脚本写入，本地始终领先，普通快进推送即可）
    push = subprocess.run(
        ["git", "push", "-q", "origin", "main"],
        cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
    )
    if push.returncode != 0:
        perr = (push.stdout or b"") + (push.stderr or b"")
        if b"non-fast-forward" in perr or b"rejected" in perr:
            print("  ↺  非快进，尝试合并远程历史后重推...")
            pull = subprocess.run(
                ["git", "pull", "-q", "origin", "main", "--allow-unrelated-histories"],
                cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
            )
            if pull.returncode == 0:
                run(["git", "push", "-q", "-u", "origin", "main"])
            else:
                print("  ⚠️  合并失败：\n", (pull.stdout or b"") + (pull.stderr or b"").decode(errors="ignore"))
        else:
            print("  ⚠️  推送失败：\n", perr.decode(errors="ignore"))

    # 7. 还原远程 URL（去掉 token，不落盘）
    if token:
        run(["git", "remote", "set-url", "origin", REMOTE_PLAIN])

    print(f"  ✅ GitHub Pages 已更新：{PAGES_URL}")
    print(f"  ✅ 归档在线回看：{PAGES_URL}archives/")


if __name__ == "__main__":
    main()
