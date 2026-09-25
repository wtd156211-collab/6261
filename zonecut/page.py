"""把一次查询的块级结果渲染成自包含单文件 HTML。

页面上的所有数字都来自引擎输出（QueryResult + BlockIndex），
页面脚本只做筛选交互，不重算任何统计。
"""

from html import escape

from .engine import HIT, SCANNED, SKIP

_STATE_LABEL = {HIT: "命中", SCANNED: "扫过", SKIP: "跳过"}


def render_page(index, result, cond):
    """返回完整 HTML 字符串。result 为 run_query 的输出。"""
    total = result.total
    scanned = result.scanned
    skipped = result.skipped
    prune_rate = (1.0 - scanned / total) if total else 0.0
    rows = []
    for block in range(index.nblocks):
        state = result.states[block]
        nrows = index.rows[block]
        empty = 1 if nrows == 0 else 0
        mn = str(index.mins[block]) if nrows else "-"
        mx = str(index.maxs[block]) if nrows else "-"
        rows.append(
            '<tr data-block="%d" data-state="%s" data-rows="%d" data-min="%s" '
            'data-max="%s" data-empty="%d"><td>%d</td><td>%s</td><td>%d</td>'
            '<td>%s</td><td>%s</td></tr>'
            % (block, state, nrows, mn, mx, empty,
               block, _STATE_LABEL[state], nrows, mn, mx))
    table_rows = "\n".join(rows)
    return _TEMPLATE.replace("__QID__", escape(result.query_id)) \
        .replace("__COND__", escape(cond.describe())) \
        .replace("__HITS__", str(len(result.hits))) \
        .replace("__SCANNED__", str(scanned)) \
        .replace("__SKIPPED__", str(skipped)) \
        .replace("__BLOCKS__", str(total)) \
        .replace("__PRUNE__", "%.1f%%" % (prune_rate * 100)) \
        .replace("__ROWS__", table_rows)


_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>zonecut 块剪枝 · __QID__</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; font-family: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; background: #f6f7f9; color: #1f2329; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .meta { color: #5a6069; font-size: 13px; margin-bottom: 16px; }
  .meta code { background: #eceef1; padding: 1px 6px; border-radius: 4px; }
  .stats { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
  .stat { background: #fff; border: 1px solid #e2e5ea; border-radius: 10px; padding: 12px 18px; min-width: 130px; }
  .stat .num { display: block; font-size: 24px; font-weight: 650; font-variant-numeric: tabular-nums; }
  .stat .label { font-size: 12px; color: #7a828d; }
  .filters { margin-bottom: 12px; font-size: 13px; }
  .filters button { border: 1px solid #d4d8de; background: #fff; border-radius: 999px; padding: 4px 14px; margin-right: 6px; cursor: pointer; font-size: 13px; }
  .filters button.active { background: #1f2329; color: #fff; border-color: #1f2329; }
  .legend { display: inline-flex; gap: 12px; margin-left: 14px; color: #5a6069; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; vertical-align: -1px; }
  .dot.hit { background: #34a853; } .dot.scanned { background: #f4b400; } .dot.skip { background: #c4c9d1; }
  table { border-collapse: collapse; width: 100%; background: #fff; font-size: 13px; font-variant-numeric: tabular-nums; }
  th, td { padding: 6px 12px; text-align: right; border-bottom: 1px solid #eef0f3; }
  th { position: sticky; top: 0; background: #fafbfc; color: #5a6069; font-weight: 600; text-align: right; }
  th:nth-child(2), td:nth-child(2) { text-align: left; }
  tbody tr { border-left: 4px solid transparent; }
  tbody tr[data-state="hit"] { background: #e8f5ec; border-left-color: #34a853; }
  tbody tr[data-state="scanned"] { background: #fdf4dc; border-left-color: #f4b400; }
  tbody tr[data-state="skip"] { background: #f0f1f4; border-left-color: #c4c9d1; color: #8a9099; }
  tbody tr.hidden { display: none; }
</style>
</head>
<body>
<header>
  <h1>块级剪枝结果</h1>
  <p class="meta">查询 <code>__QID__</code> · 条件 <code>__COND__</code> · 命中 __HITS__ 行</p>
  <div class="stats">
    <div class="stat" data-key="scanned"><span class="num">__SCANNED__</span><span class="label">扫描块数</span></div>
    <div class="stat" data-key="skipped"><span class="num">__SKIPPED__</span><span class="label">跳过块数</span></div>
    <div class="stat" data-key="blocks"><span class="num">__BLOCKS__</span><span class="label">总块数</span></div>
    <div class="stat" data-key="prune-rate"><span class="num">__PRUNE__</span><span class="label">剪枝率</span></div>
  </div>
  <div class="filters">
    <button data-filter="all" class="active">全部</button><button data-filter="hit">命中</button><button data-filter="scanned">扫过</button><button data-filter="skip">跳过</button><span class="legend"><span><span class="dot hit"></span>命中</span><span><span class="dot scanned"></span>扫过</span><span><span class="dot skip"></span>跳过</span></span>
  </div>
</header>
<table id="blocks">
  <thead><tr><th>块号</th><th>状态</th><th>行数</th><th>min_ts</th><th>max_ts</th></tr></thead>
  <tbody>
__ROWS__
  </tbody>
</table>
<script type="module">
const buttons = document.querySelectorAll(".filters button");
const rows = document.querySelectorAll("#blocks tbody tr");
for (const button of buttons) {
  button.addEventListener("click", () => {
    for (const b of buttons) b.classList.toggle("active", b === button);
    const filter = button.dataset.filter;
    for (const row of rows) {
      row.classList.toggle("hidden", filter !== "all" && row.dataset.state !== filter);
    }
  });
}
</script>
</body>
</html>
"""
