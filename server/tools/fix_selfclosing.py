#!/usr/bin/env python3
"""修复 HTML 中的自闭合自定义组件标签。

问题:在普通 HTML 里写 <el-input .../> 时,HTML 解析器不认识自闭合的自定义标签,
会把后续兄弟节点吞成子节点,导致 Vue 读到的模板结构错乱(表单字段丢失/嵌套)。
修法:把 <el-xxx .../> 展开成 <el-xxx ...></el-xxx>(只处理 el- 前缀,不碰 void 元素)。
"""
import re
import pathlib
import sys

WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
PAT = re.compile(r"<(el-[a-z0-9-]+)((?:\s[^<>]*?)?)\s*/>", re.S)  # noqa: E501


def fix(text: str) -> str:
    def repl(m):
        tag, attrs = m.group(1), m.group(2) or ""
        # 去掉属性尾部空白,避免出现 "attr >" 这种非规范写法
        return "<%s%s></%s>" % (tag, attrs.rstrip(), tag)
    return PAT.sub(repl, text)


def main() -> int:
    changed = 0
    for f in sorted(WEB.glob("*.html")):
        src = f.read_text(encoding="utf-8")
        out = fix(src)
        if out != src:
            before = len(PAT.findall(src))
            f.write_text(out, encoding="utf-8")
            print("已修复 %-14s 展开 %d 处自闭合标签" % (f.name, before))
            changed += 1
    if not changed:
        print("无需修复")
    return 0


if __name__ == "__main__":
    sys.exit(main())
