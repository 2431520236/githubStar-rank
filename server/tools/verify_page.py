"""页面渲染验证:加载首页,检查 DOM 渲染 + 接口数据 + 截图。

用法:python3.12 verify_page.py [url] [out.png]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9951/"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/page.png"

errors: list[str] = []
failed: list[str] = []

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    page = browser.new_page(viewport={"width": 1680, "height": 950})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("requestfailed",
            lambda r: failed.append(f"{r.url} :: {r.failure}"))

    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3500)

    # ---- 几何/内容断言 ----
    checks = {
        "顶栏存在": "document.querySelector('.topbar') !== null",
        "品牌文字": "!!document.querySelector('.brand') && document.querySelector('.brand').innerText.includes('飙升雷达')",
        "导航 5 项": "document.querySelectorAll('.nav a').length === 5",
        "三列栅格": "getComputedStyle(document.querySelector('.screen')).gridTemplateColumns.split(' ').length === 3",
        "KPI 4 卡": "document.querySelectorAll('.kpi').length === 4",
        "KPI 有数值": "document.querySelector('.kpi .val').innerText.trim().length > 0",
        "语言图已渲染": "!!document.querySelector('#chart-lang canvas')",
        "协议图已渲染": "!!document.querySelector('#chart-license canvas')",
        "周趋势图已渲染": "!!document.querySelector('#chart-week canvas')",
        "体量图已渲染": "!!document.querySelector('#chart-bucket canvas')",
        "有排名行": "document.querySelectorAll('.rank-row').length > 0",
        "无 Vue 未渲染标记": "!document.querySelector('[v-cloak]')",
    }
    print("=== DOM 断言 ===")
    ok = 0
    for name, js in checks.items():
        try:
            r = page.evaluate(f"!!({js})")
        except Exception as e:  # noqa: BLE001
            r = False
            print(f"  [ERR] {name}: {e}")
        print(f"  [{'OK ' if r else 'FAIL'}] {name}")
        ok += 1 if r else 0

    print(f"  → {ok}/{len(checks)} 通过")

    # ---- 抓取页面上的实际数据 ----
    info = page.evaluate("""() => ({
        title: document.title,
        kpis: [...document.querySelectorAll('.kpi')].map(k => k.querySelector('.label').innerText + '=' + k.querySelector('.val').innerText),
        navs: [...document.querySelectorAll('.nav a')].map(a => a.innerText),
        rankRows: document.querySelectorAll('.rank-row').length,
        firstRepo: (document.querySelector('.rank-row .name')||{}).innerText || '',
        charts: document.querySelectorAll('canvas').length
    })""")
    print("=== 页面数据 ===")
    for k, v in info.items():
        print(f"  {k}: {v}")

    page.screenshot(path=OUT, full_page=False)
    print(f"=== 截图:{OUT} ===")
    browser.close()

if errors:
    print("=== 控制台错误 ===")
    for e in errors[:10]:
        print("  !", e)
if failed:
    print("=== 资源加载失败 ===")
    for f in failed[:10]:
        print("  !", f)
print("RESULT:", "PASS" if ok == len(checks) else "PARTIAL")
