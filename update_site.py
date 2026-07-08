#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日更新脚本 — 统一处理三个频道（AI / Display / Paper）。
- 读 stdin 接收 ai-news-daily/v1、display-news-daily/v1 或 display-paper-daily/v1 JSON
- 写入 data/<channel>/YYYY-MM-DD.json
- 同步 data/<channel>/latest.json
- 对 paper 频道的 attachments[] PDF 尝试本地缓存，失败保留原链
- 扫描历史，生成 HISTORY 清单（最多 14 天）
- 清理 > 14 天的旧文件
- 把数据注入到 index.html（统一 SPA）
- 同步 .workbuddy/memory/YYYY-MM-DD.md
"""
import json
import os
import sys
import re
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

# ===== 路径 =====
BASE_DIR = Path(r"C:\Users\17210\WorkBuddy\automation-2026-06-24-09-25-21")
TEMPLATE_FILE = BASE_DIR / "index.template.html"
INDEX_FILE = BASE_DIR / "index.html"
DATA_DIR = BASE_DIR / "data"
AI_DIR = DATA_DIR / "ai"
DISPLAY_DIR = DATA_DIR / "display"
PAPER_DIR = DATA_DIR / "paper"
SEMICON_DIR = DATA_DIR / "semicon"
PAPER_ATTACH_DIR = PAPER_DIR / "attachments"
MEMORY_DIR = BASE_DIR / ".workbuddy" / "memory"
ARCHIVE_DIR = Path(r"W:\DailyNews")   # 用户自定义本地归档位置（不在项目目录内）
MAX_KEEP_DAYS = 14
WINDOW_DAYS = 14                      # 归档窗口长度 = 保存范围（与 MAX_KEEP_DAYS 对齐）
WINDOW_STATE = ARCHIVE_DIR / ".window.json"   # 记录当前窗口起止，避免每日改名

AI_DIR.mkdir(parents=True, exist_ok=True)
DISPLAY_DIR.mkdir(parents=True, exist_ok=True)
PAPER_DIR.mkdir(parents=True, exist_ok=True)
SEMICON_DIR.mkdir(parents=True, exist_ok=True)
PAPER_ATTACH_DIR.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

# 下载 PDF 用的请求头
PDF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def detect_channel(schema: str) -> str:
    if "ai-news-daily" in schema:
        return "ai"
    if "display-news-daily" in schema:
        return "display"
    if "display-paper-daily" in schema:
        return "paper"
    if "semicon-news-daily" in schema:
        return "semicon"
    if "semicon-paper-daily" in schema:
        return "semicon"
    raise ValueError(f"未知 schema: {schema}")


def load_all(channel_dir: Path) -> dict:
    """加载某个频道的所有历史 JSON,key 为日期."""
    out = {}
    for f in sorted(channel_dir.glob("*.json")):
        if f.name == "latest.json":
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.json$", f.name)
        if not m:
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                out[m.group(1)] = json.load(fp)
        except Exception as e:
            print(f"  ! 跳过损坏文件 {f.name}: {e}", file=sys.stderr)
    return out


def cleanup_old_files(channel_dir: Path, keep_dates: set):
    """删除不在 keep_dates 里的旧日期文件."""
    for f in channel_dir.glob("*.json"):
        if f.name == "latest.json":
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.json$", f.name)
        if m and m.group(1) not in keep_dates:
            print(f"  🗑️  清理过期文件: {channel_dir.name}/{f.name}")
            f.unlink()


def cleanup_old_attachments(cutoff_date: str):
    """清理附件目录里早于 cutoff_date 的子目录."""
    if not PAPER_ATTACH_DIR.exists():
        return
    for d in PAPER_ATTACH_DIR.iterdir():
        if d.is_dir() and re.match(r"\d{4}-\d{2}-\d{2}$", d.name):
            if d.name < cutoff_date:
                print(f"  🗑️  清理过期附件目录: data/paper/attachments/{d.name}")
                import shutil
                shutil.rmtree(d, ignore_errors=True)


def try_find_oa_pdf(doi: str, timeout: int = 20) -> str | None:
    """通过 Unpaywall API 查找开放获取 PDF."""
    if not doi:
        return None
    email = os.environ.get("UNPAYWALL_EMAIL", "workbuddy.user@gmail.com")
    url = f"https://api.unpaywall.org/v2/{urllib.request.quote(doi)}?email={email}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": PDF_HEADERS["User-Agent"]})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            loc = data.get("best_oa_location") or {}
            pdf_url = loc.get("url_for_pdf") or loc.get("url")
            if pdf_url and pdf_url.lower().endswith(".pdf"):
                return pdf_url
    except Exception as e:
        print(f"    ⚠️  Unpaywall 查询失败 {doi}: {e}")
    return None


def extract_doi(url: str) -> str | None:
    """从论文链接中提取 DOI."""
    if not url:
        return None
    # doi.org/10.xxxx/...
    m = re.search(r"(?:doi\.org/|/doi/|10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", url)
    if m:
        doi = m.group(0)
        if doi.startswith("doi.org/"):
            doi = doi[len("doi.org/"):]
        elif doi.startswith("/doi/"):
            doi = doi[len("/doi/"):]
        if re.match(r"^10\.\d{4,9}/", doi):
            return doi
def download_pdf(url: str, save_path: Path, timeout: int = 35) -> bool:
    """下载 PDF 到本地,返回是否成功."""
    if not url:
        return False
    try:
        req = urllib.request.Request(url, headers=PDF_HEADERS, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "").lower()
            data = resp.read()
            if not data:
                return False
            # 如果是 PDF,前面几个字节是 %PDF
            if not data.startswith(b"%PDF") and not content_type.startswith("application/pdf"):
                print(f"    ⚠️  {url} 返回的不是 PDF,跳过本地缓存")
                return False
            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, "wb") as f:
                f.write(data)
        return True
    except urllib.error.HTTPError as e:
        print(f"    ⚠️  下载失败 {url}: HTTP {e.code}")
    except Exception as e:
        print(f"    ⚠️  下载失败 {url}: {e}")
    return False


def cache_paper_attachments(payload: dict, date_str: str) -> dict:
    """
    对 paper 频道的每篇论文 attachments 尝试下载 PDF 到本地。
    成功则把 url 替换为相对路径 ./data/paper/attachments/YYYY-MM-DD/{paper-id}-{n}.pdf。
    失败保留原 url,并标记 cached=false。
    """
    papers = payload.get("papers") or []
    for idx, p in enumerate(papers, start=1):
        attachments = p.get("attachments") or []
        for a in attachments:
            url = a.get("url", "")
            if not url.lower().endswith(".pdf"):
                a["cached"] = False
                continue
            ext = ".pdf"
            safe_label = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", a.get("label", "PDF") or "PDF")[:30]
            filename = f"paper-{idx}-{safe_label}{ext}"
            save_path = PAPER_ATTACH_DIR / date_str / filename
            if save_path.exists():
                a["url"] = f"./data/paper/attachments/{date_str}/{filename}"
                a["cached"] = True
                print(f"    ✅ 附件已缓存: {filename}")
                continue

            # 第一次尝试：直接下载提供的 URL
            success = download_pdf(url, save_path)

            # 第二次尝试：如果失败且有 DOI，用 Unpaywall 找 OA PDF
            if not success:
                doi = extract_doi(p.get("url", ""))
                if doi:
                    oa_url = try_find_oa_pdf(doi)
                    if oa_url:
                        print(f"    🔍 通过 Unpaywall 找到 OA PDF: {oa_url}")
                        success = download_pdf(oa_url, save_path)

            if success:
                a["url"] = f"./data/paper/attachments/{date_str}/{filename}"
                a["cached"] = True
                print(f"    ✅ 附件缓存成功: {filename}")
            else:
                a["cached"] = False
                print(f"    ❌ 附件缓存失败,保留原链: {url}")
    return payload


def build_history(ai_data: dict, display_data: dict, paper_data: dict, semicon_data: dict, max_days=MAX_KEEP_DAYS):
    """合并四个频道的日期,按日期倒序,只保留最近 max_days 天."""
    all_dates = set(ai_data.keys()) | set(display_data.keys()) | set(paper_data.keys()) | set(semicon_data.keys())
    sorted_dates = sorted(all_dates, reverse=True)[:max_days]
    history = []
    for d in sorted_dates:
        chs = []
        if d in display_data:
            chs.append("display")
        if d in ai_data:
            chs.append("ai")
        if d in paper_data:
            chs.append("paper")
        if d in semicon_data:
            chs.append("semicon")
        entry = {"date": d, "channels": chs}
        # 优先用 display 的 weekday 作为主显示
        if d in display_data:
            entry["weekday"] = display_data[d].get("weekday", "")
            entry["note"] = display_data[d].get("weekday", "")
        elif d in semicon_data:
            entry["weekday"] = semicon_data[d].get("weekday", "")
            entry["note"] = semicon_data[d].get("weekday", "")
        elif d in ai_data:
            entry["weekday"] = ai_data[d].get("weekday", "")
            entry["note"] = ai_data[d].get("weekday", "")
        elif d in paper_data:
            entry["weekday"] = paper_data[d].get("weekday", "")
            entry["note"] = paper_data[d].get("weekday", "")
        history.append(entry)
    return history, sorted_dates


def write_memory_log(date_str: str, payload: dict, channel: str):
    """同步生成 .workbuddy/memory/YYYY-MM-DD.md 摘要."""
    md_file = MEMORY_DIR / f"{date_str}.md"
    channel_label = {"ai": "🤖 AI", "display": "📺 面板显示", "paper": "📄 论文技术", "semicon": "🔬 半导体"}[channel]
    note = f"\n\n---\n\n## {channel_label} {date_str}\n\n"
    note += f"**摘要**：{payload.get('summary','')}\n\n"
    if payload.get("news") or payload.get("papers"):
        items = payload.get("news") or payload.get("papers")
        note += f"**条目**：{len(items)} 条\n"
    if channel == "ai" and payload.get("skills"):
        note += f"**Skill 推荐**：{len(payload['skills'])} 条\n"
    if channel == "display" and payload.get("routes"):
        note += f"**技术路线**：{len(payload['routes'])} 条\n"
    if channel == "paper" and payload.get("routes"):
        note += f"**技术路线**：{len(payload['routes'])} 条\n"
    if channel == "paper" and payload.get("papers"):
        cached = sum(1 for p in payload["papers"] for a in (p.get("attachments") or []) if a.get("cached"))
        total = sum(len(p.get("attachments") or []) for p in payload["papers"])
        note += f"**附件缓存**：{cached}/{total} 个 PDF 已本地缓存\n"

    with open(md_file, "a", encoding="utf-8") as f:
        f.write(note)


def render_index(ai_data: dict, display_data: dict, paper_data: dict, semicon_data: dict, history: list, out_file: Path = INDEX_FILE):
    """渲染 SPA index.html,内嵌四个频道的数据 + HISTORY。可指定输出路径（用于窗口归档快照）。"""
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    ai_json = json.dumps(ai_data, ensure_ascii=False)
    display_json = json.dumps(display_data, ensure_ascii=False)
    paper_json = json.dumps(paper_data, ensure_ascii=False)
    semicon_json = json.dumps(semicon_data, ensure_ascii=False)
    history_json = json.dumps(history, ensure_ascii=False)

    html = template.replace('"__AI_DATA_PLACEHOLDER__"', ai_json)
    html = html.replace('"__DISPLAY_DATA_PLACEHOLDER__"', display_json)
    html = html.replace('"__PAPER_DATA_PLACEHOLDER__"', paper_json)
    html = html.replace('"__SEMICON_DATA_PLACEHOLDER__"', semicon_json)
    html = html.replace("__HISTORY_PLACEHOLDER__", history_json)

    out_file.write_text(html, encoding="utf-8")
    print(f"  ✅ 渲染 {out_file.name}  ({len(html)} bytes)")


def save_payload(channel: str, date_str: str, payload: dict):
    """写入 channel/YYYY-MM-DD.json + latest.json."""
    target_dir = {"ai": AI_DIR, "display": DISPLAY_DIR, "paper": PAPER_DIR, "semicon": SEMICON_DIR}[channel]
    date_file = target_dir / f"{date_str}.json"
    latest_file = target_dir / "latest.json"
    with open(date_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  💾 {channel}/{date_str}.json (latest.json 已同步)")


def get_or_roll_window(today: datetime) -> dict:
    """返回当前 14 天归档窗口 {start, end}（字符串 YYYY-MM-DD）。
    窗口起始固定；仅当 today > 当前窗口 end 时才滚动到新窗口，
    从而保证窗口名称在一个保存周期内稳定（'固定命名，按照保存范围'）。"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    today_s = today.strftime("%Y-%m-%d")
    cur = None
    if WINDOW_STATE.exists():
        try:
            cur = json.loads(WINDOW_STATE.read_text(encoding="utf-8"))
        except Exception:
            cur = None
    if cur and cur.get("start") and cur.get("end") and today_s <= cur["end"]:
        return cur
    start = today_s
    end = (today + timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    nw = {"start": start, "end": end}
    WINDOW_STATE.write_text(json.dumps(nw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  🪟 新归档窗口：{start} ~ {end}（每 {WINDOW_DAYS} 天一个，固定命名）")
    return nw


def window_folder_name(start: str, end: str) -> str:
    """(20260707~20260721.DailyNews) 形式，无连字符、紧凑。"""
    return f"({start.replace('-', '')}~{end.replace('-', '')}.DailyNews)"


def archive_window(start: str, end: str, ai_data: dict, display_data: dict, paper_data: dict, semicon_data: dict):
    """把当前窗口 [start, end] 内全部频道数据渲染成自包含 index.html，
    存到 W:\\DailyNews\\(START~END.DailyNews)\\，永久本地留存并供 GitHub Pages 浏览。
    每个工作日运行都会重渲染该窗口，使其随新内容累积更新；窗口结束后冻结。
    仅当窗口内至少有某一频道数据时归档。"""
    import shutil
    in_win = lambda d: start <= d <= end
    ai_w = {d: v for d, v in ai_data.items() if in_win(d)}
    disp_w = {d: v for d, v in display_data.items() if in_win(d)}
    paper_w = {d: v for d, v in paper_data.items() if in_win(d)}
    semi_w = {d: v for d, v in semicon_data.items() if in_win(d)}
    if not (ai_w or disp_w or paper_w or semi_w):
        print(f"  🗂️  归档跳过：窗口 {start}~{end} 暂无数据")
        return
    history, _ = build_history(ai_w, disp_w, paper_w, semi_w, max_days=WINDOW_DAYS)
    folder = ARCHIVE_DIR / window_folder_name(start, end)
    folder.mkdir(parents=True, exist_ok=True)
    # 渲染自包含 index.html 到窗口目录（可离线/在 Pages 上按日期浏览整段窗口）
    render_index(ai_w, disp_w, paper_w, semi_w, history, out_file=folder / "index.html")
    # 复制各频道窗口内 JSON（日期→内容 映射，便于程序复用）
    manifest = {
        "window": f"{start}~{end}",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "channels": {},
    }
    for ch, dmap in [("ai", ai_w), ("display", disp_w), ("paper", paper_w), ("semicon", semi_w)]:
        if dmap:
            (folder / f"{ch}.json").write_text(json.dumps(dmap, ensure_ascii=False, indent=2), encoding="utf-8")
            items = sum(len(v.get("news") or v.get("papers") or []) for v in dmap.values())
            manifest["channels"][ch] = {
                "days": len(dmap),
                "items": items,
                "skills": sum(len(v.get("skills", [])) for v in dmap.values()),
            }
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  🗂️  已归档窗口快照 → W:\\DailyNews\\{folder.name}/（{len(history)} 天，永久留存 + 可推 GitHub Pages）")


def main():
    raw = sys.stdin.read().strip()
    if not raw:
        print("⚠️  stdin 为空,跳过本次更新")
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析失败: {e}")
        sys.exit(1)

    schema = payload.get("schema", "")
    channel = detect_channel(schema)
    date_str = payload.get("date") or datetime.now().strftime("%Y-%m-%d")
    print(f"\n📅 处理 {channel} 频道 · {date_str}")

    # 1. paper 频道先尝试缓存附件 PDF
    if channel == "paper":
        print("  📥 尝试缓存论文附件 PDF...")
        payload = cache_paper_attachments(payload, date_str)

    # 2. 保存本次 payload
    save_payload(channel, date_str, payload)

    # 3. 写 memory 日志
    write_memory_log(date_str, payload, channel)

    # 4. 加载所有历史
    ai_data = load_all(AI_DIR)
    display_data = load_all(DISPLAY_DIR)
    paper_data = load_all(PAPER_DIR)
    semicon_data = load_all(SEMICON_DIR)

    # 5. 清理过期文件(> 14 天)
    today = datetime.strptime(date_str, "%Y-%m-%d")
    cutoff = (today - timedelta(days=MAX_KEEP_DAYS - 1)).strftime("%Y-%m-%d")
    keep_dates = set()
    for d in list(ai_data.keys()) + list(display_data.keys()) + list(paper_data.keys()) + list(semicon_data.keys()):
        if d >= cutoff:
            keep_dates.add(d)
    cleanup_old_files(AI_DIR, keep_dates)
    cleanup_old_files(DISPLAY_DIR, keep_dates)
    cleanup_old_files(PAPER_DIR, keep_dates)
    cleanup_old_files(SEMICON_DIR, keep_dates)
    cleanup_old_attachments(cutoff)

    # 6. 重新加载(清理后)
    ai_data = load_all(AI_DIR)
    display_data = load_all(DISPLAY_DIR)
    paper_data = load_all(PAPER_DIR)
    semicon_data = load_all(SEMICON_DIR)

    # 7. 生成 HISTORY 清单
    history, kept_dates = build_history(ai_data, display_data, paper_data, semicon_data)
    print(f"  📋 HISTORY: {len(history)} 个日期,范围 {kept_dates[-1]} ~ {kept_dates[0]}")

    # 8. 渲染 index.html（线上站点，CloudStudio 部署用）
    render_index(ai_data, display_data, paper_data, semicon_data, history)

    # 9. 归档当前 14 天窗口完整快照（固定命名按保存范围，永久本地留存 + 供 GitHub Pages 浏览）
    win = get_or_roll_window(today)
    archive_window(win["start"], win["end"], ai_data, display_data, paper_data, semicon_data)

    print(f"\n✅ 完成。访问 {INDEX_FILE} 即可查看。\n")


if __name__ == "__main__":
    main()
