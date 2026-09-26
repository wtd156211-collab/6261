"""自包含单文件 HTML：逐块一行，数字只来自引擎输出。"""

from html import escape

_STATE_LABEL = {"hit": "命中", "scanned": "扫描", "skip": "跳过"}

_CSS = """
body { font-family: system-ui, sans-serif; margin: 2rem; color: #222; }
header { margin-bottom: 1.2rem; }
h1 { font-size: 1.3rem; margin: 0 0 .8rem; }
.stats { display: flex; gap: 1.5rem; margin: 0; }
.stats div { background: #f4f4f4; border-radius: 8px; padding: .6rem 1rem; }
.stats dt { font-size: .8rem; color: #666; }
.stats dd { margin: .2rem 0 0; font-size: 1.4rem; font-weight: 600; }
table { border-collapse: collapse; width: 100%; font-size: .85rem; }
th, td { padding: .3rem .7rem; text-align: right; border-bottom: 1px solid #e5e5e5; }
th { background: #fafafa; position: sticky; top: 0; }
tr[data-state="hit"] { background: #d8f3dc; }
tr[data-state="scanned"] { background: #fff3cd; }
tr[data-state="skip"] { background: #e9ecef; color: #868e96; }
tr.picked { outline: 2px solid #4263eb; outline-offset: -2px; }
.legend { margin: .6rem 0 1rem; font-size: .8rem; color: #666; }
.sw { display: inline-block; width: .8em; height: .8em; border-radius: 2px;
      margin: 0 .3em 0 1em; vertical-align: baseline; }
"""

_SCRIPT = """
// 仅做装饰性交互（点击高亮一行），所有数字都来自引擎输出，页面不重算。
document.getElementById("blocks").addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-block]");
  if (row) row.classList.toggle("picked");
});
"""


def render_page(query_id, hits_count, scanned, states):
    """states: 每块 (state, rows, min_ts, max_ts, empty)，按块号升序。"""
    total = len(states)
    skipped = total - scanned
    if total == 0:
        rate = "100.0%"
    else:
        rate = f"{(1 - scanned / total) * 100:.1f}%"
    qid = escape(query_id)

    rows_html = []
    for b, (state, n, m, M, empty) in enumerate(states):
        min_s = "-" if empty else str(m)
        max_s = "-" if empty else str(M)
        rows_html.append(
            f'<tr data-block="{b}" data-state="{state}" data-rows="{n}" '
            f'data-min="{min_s}" data-max="{max_s}" data-empty="{empty}">'
            f"<td>{b}</td><td>{_STATE_LABEL[state]}</td><td>{n}</td>"
            f"<td>{min_s}</td><td>{max_s}</td>"
            f"<td>{'是' if empty else '否'}</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>zonecut 块剪枝 · {qid}</title>
<style>{_CSS}</style>
</head>
<body>
<header>
<h1>zonecut 块剪枝 · 查询 <code>{qid}</code> · 命中 {hits_count} 行</h1>
<dl class="stats">
<div><dt>扫描块数</dt><dd data-key="scanned">{scanned}</dd></div>
<div><dt>跳过块数</dt><dd data-key="skipped">{skipped}</dd></div>
<div><dt>总块数</dt><dd data-key="blocks">{total}</dd></div>
<div><dt>剪枝率</dt><dd data-key="prune-rate">{rate}</dd></div>
</dl>
<p class="legend">块状态：<span class="sw" style="background:#d8f3dc"></span>命中
<span class="sw" style="background:#fff3cd"></span>扫描无命中
<span class="sw" style="background:#e9ecef"></span>跳过（含空块）</p>
</header>
<table id="blocks">
<thead><tr><th>块号</th><th>状态</th><th>行数</th><th>min_ts</th><th>max_ts</th><th>空块</th></tr></thead>
<tbody>
{"".join(rows_html)}
</tbody>
</table>
<script type="module">{_SCRIPT}</script>
</body>
</html>
"""
