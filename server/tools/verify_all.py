"""全站页面验证:逐个页面检查 HTTP + DOM 渲染 + 控制台错误,并截图。

用法:python3.12 verify_all.py [base_url] [outdir]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9951"
OUTDIR = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/ghradar")
OUTDIR.mkdir(parents=True, exist_ok=True)

PAGES = [
    ("首页-数据大屏", "/", ".topbar", ".kpi"),
    ("榜单-筛选", "/rank.html", ".topbar", ".link-row-table"),
    ("项目详情", "/detail.html?repo=microsoft/markitdown", ".topbar", ".detail-head"),
    ("模型与任务", "/ai.html", ".topbar", ".el-tabs"),
    ("关注提醒", "/watch.html", ".topbar", ".panel"),
    ("采集与调度", "/system.html", ".topbar", ".el-tabs"),
]

allok = True
with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    for title, path, must_sel, data_sel in PAGES:
        errs = []
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        resp = page.goto(BASE + path, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(2200)
        status = resp.status if resp else 0

        has_nav = page.evaluate(f"!!document.querySelector('{must_sel}')")
        has_data = page.evaluate(f"!!document.querySelector('{data_sel}')")
        # 导航 5 项 + 无 v-cloak 残留
        navs = page.evaluate("document.querySelectorAll('.nav a').length")
        cloaked = page.evaluate("!!document.querySelector('[v-cloak]')")
        canvases = page.evaluate("document.querySelectorAll('canvas').length")

        ok = status == 200 and has_nav and has_data and navs == 5 and not cloaked
        allok = allok and ok
        shot = OUTDIR / (path.strip("/").replace("/", "_").replace("?", "_").replace("&", "_")
                         .split(".")[0] or "index")
        page.screenshot(path=str(shot.with_suffix(".png")), full_page=False)
        print(f"[{'OK ' if ok else 'FAIL'}] {title:14s} {path:44s} "
              f"http={status} nav={navs} 数据块={has_data} canvas={canvases} "
              f"console_err={len(errs)}")
        for e in errs[:3]:
            print("        ! ", e[:140])
        page.close()
    browser.close()

print("\nRESULT:", "ALL PASS" if allok else "HAS FAILURES")
sys.exit(0 if allok else 1)
