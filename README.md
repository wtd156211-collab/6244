# wt-036 safetmpl 模板渲染与上下文转义（从 0 实现）

起始环境只有说明与素材（见第 4 节的文件清单），`web/` 空着，引擎与页面从零写。

## 1. 范围

做的：模板引擎（`python -m safetmpl`）、四种上下文转义、错误码与位置、逐字节可比的渲染结果与展示页面。不做：
宏、过滤器、表达式、多级继承、嵌套 block、`{% raw %}`/`{% set %}` 这类语句；网络、数据库、缓存、并发；第三方
库、CDN、构建；页面写回；CSS/JS 语义检查；非法素材容错（`samples/` 合法）。

## 2. 口径与公式

### 2.1 模板语法

变量 `{{ 路径 }}`、`{{ 路径 | raw }}`；路径 = `名字 ('.' 名字 | '[' 整数 ']')*`（名字 `[A-Za-z_][A-Za-z0-9_]*`）；
注释 `{# … #}` 不输出。条件 `{% if 路径 %}`、`{% elif %}`、`{% else %}`、`{% endif %}` 只判单路径真假（可加
`not`），假值 `null`、`false`、`0`、`""`、`[]`、`{}`。循环 `{% for 名字 in 路径 %}` … `{% endfor %}`，路径必须是
列表，循环体可用 `loop.index`（从 1 起）、`loop.first`、`loop.last`。包含 `{% include "名字.html" %}`（模板同
目录，`_` 开头是片段）。继承 `{% extends "_base.html" %}` 必须是第一条语句，父模板 `{% block 名字 %}`
给默认内容、子模板同名 block 覆盖，子模板除 extends/block/注释/空白外不能有别的内容，block 名必须在父模板里
存在。上述之外一律报错：过滤器、表达式、比较与逻辑、`for` 的 else/过滤/排序/解构、嵌套 block、多级继承、单引号
或无引号属性值。脚本块只支持内联 `<script>`，带 `src` 的不在范围内。

### 2.2 转义口径

| 上下文 | 替换 |
| --- | --- |
| 标签之间（`text` 状态） | `&`→`&amp;`、`<`→`&lt;`、`>`→`&gt;`，不动引号 |
| 属性（双引号属性值，属性名不在链接名单） | `&`、`"`、`<`、`>` → `&amp;`、`&quot;`、`&lt;`、`&gt;` |
| 链接地址（`href`/`src`/`action`/`formaction`/`poster`/`cite`/`xlink:href` 的值） | 去掉 ASCII 控制字符与空白后取 `:` 前那段，形状是 `[A-Za-z][A-Za-z0-9+.-]*` 就小写比对，放行 `http`/`https`/`mailto`/`tel`，否则整串换 `#`；放行或没有 `:` 的再按属性口径转义 |
| 脚本块（`<script>` 与 `</script>` 之间） | 先 JSON 字符串序列化（非 ASCII 与控制字符写 `\uXXXX`），再把 `<`、`>`、`&` 写成 `\u003c`、`\u003e`、`\u0026` |

替换一对一并先换 `&`；不做百分号/实体解码；`| raw` 只在标签之间生效，用在别处报 `E_CTX`。

### 2.3 作用域与继承

作用域是链：根帧是数据键，`for` 每轮压一帧放循环变量与 `loop`（内层遮蔽外层、循环结束复原），`if` 不引入
名字，查找由内往外。`include` 不新建作用域、看到调用点的名字，展开处接着调用点的上下文算。继承只把父
模板 block 整段换成子模板的 block 内容。

### 2.4 错误码与报错位置

`<路径>:<行>:<列>: <码>: <说明>`，行列从 1 起、按 Unicode 码点计，指向出错标签的 `{`；错在被展开的模板里时追加
`(来自 <展开者>:<行>)`（多层用 ` <- ` 连接、最内层在前）。错误写 stderr 一行、退出码 2，不产出输出文件。码：
`E_SYNTAX` 语法与不支持写法、`E_NAME` 名字、`E_TPL` 找不到模板、`E_RECURSE` 成环或超 16 层、`E_CTX` 变量位置、
`E_DATA` 缺变量/字段/下标越界、`E_TYPE` 类型、`E_IO` 读不到文件或数据不合法。判定分两趟：入口模板与继承链先
整体解析，再按输出顺序渲染；include 的片段在展开到它时才解析。

## 3. 状态机与数据结构

渲染对着输出字符流跑状态机：`text`（标签之间）、`tag`（`<` 与 `>` 之间；`text` 里 `<` 后紧跟 ASCII 字母或
`/` 才进）、`attr`（双引号属性值内）、`script`（`<script>` 与 `</script>` 之间）；`>` 回 `text`，`attr` 遇
`"`、`script` 遇 `</script` 各回上一级。变量输出取那一刻的状态，`tag` 里报 `E_CTX`；状态机不因 include、
block、循环边界重置，结束必须回到 `text`。
分段记录按输出顺序排，每段有 `kind`、`context`、`escape`、`tpl`、`path`、`replaced`（每项
`[原字符, 替换后, 次数]`）与可选 `note`（取值见上），值段另有 `raw`/`text`。

## 4. 输入输出与文件格式

素材 UTF-8、无 BOM、单 `\n`、末尾换行，数据顶层是对象：`samples/templates/*.html`、`samples/data/*.json`、
`samples/expected/` 的 `<名>.html` 或 `<名>.err.txt`、`samples/injection/` 的 `<名>.html` 加同名 `.json` 与
`.out.html`。

```
python -m safetmpl render <模板> <数据 JSON> <输出 HTML>
python -m safetmpl page   <页面 HTML>
```

在仓库根目录跑（退出码见 2.4）；`render` 逐字节等于期望文件；`page` 把全部入口模板与注入用例内联成一个页面：
`python -m safetmpl page var/page.html` 后双击 `var/page.html` 即开（自包含、不 fetch、不引 CDN），也可
`python -m http.server 8000` 开 `http://127.0.0.1:8000/var/page.html`。页面必现：① 每个样例一块，渲染结果按
上面的分段列出，标出所属上下文（标签之间／属性／链接地址／脚本块）与实际用到的转义方式（`literal`／
`escape_text`／`escape_attr`／`escape_url`／`escape_script`／`raw`）；② 替换前后与次数可见，链接换 `#` 时写
原因；③ 报错样例显示那行错误；④ 内联 SVG：按上下文着色的横条、图例与段数；⑤ 注入用例单独一组，显示值段写出
文本里 `<`、`>`、`javascript:`、`</script` 的命中次数（都必须是 0）。跑两遍逐字节相同。

## 5. 性能与验收口径

规模：单模板 ≤ 200 KB、数据 ≤ 1 MB、结果 ≤ 8 MB、循环项 ≤ 10^4；`render` ≤ 1 秒、`page` ≤ 3 秒，额外峰值内存
≤ 64 MiB（`tracemalloc` 扣基线）；Python 3.13、只用标准库、无构建无网络。

1. 正确性：渲染结果与 `samples/expected/<名>.html` 逐字节相同（含末尾换行）；报错样例与 `<名>.err.txt` 相同、
   退出码 2。
2. 上下文与注入：四种上下文都跑到；注入用例与 `<名>.out.html` 逐字节相同，值段写出的文本不出现 `<`、`>`（脚本
   段是 `\u003c`、`\u003e`）与 `javascript:`、`</script`。
3. 页面与规模：双击能开、五项齐全、数字与分段一致、两遍逐字节相同；`unittest` 用例只读 `samples/`，放大数据
   自备。

## 6. 样例说明

每组 `<名>` 是 `samples/templates/<名>.html` + `samples/data/<名>.json`，期望在 `samples/expected/`；数字是值段
条数与替换处数：

- `notice`：text 12 / attr 2 / url 5 / script 3 段，替换 28 处；四种上下文 + 循环 + 条件 + include。
- `gallery`：text 6 / attr 4 / url 3 / script 0 段，替换 12 处；继承 + 循环里 include + `| raw`。
- `scopes`：text 16 / attr 0 / url 0 / script 2 段，替换 10 处；变量遮蔽与复原 + include 读循环变量。
- 报错：`missing` `E_DATA`（片段里，附 `(来自 …missing.html:3)`）、`syntax` `E_SYNTAX`、`ctx` `E_CTX`、
  `recurse` `E_RECURSE`、`type` `E_TYPE`。
- `samples/injection/`：`quote` 引号、`angle` 尖括号、`scheme` 协议伪造、`attr` 属性逃逸、`script` 的
  `</script>` 与引号；`notes.md` 是现场记录。

核对（仓库根目录）：`python -m safetmpl render samples/templates/notice.html samples/data/notice.json
var/notice.html` 与 `samples/expected/notice.html` 逐字节比（`Get-FileHash` 或 `cmp`）；`missing` 那条还要看退出
码 2 与 stderr 是否等于 `samples/expected/missing.err.txt`。

## 7. 待补的文档

`style`/`srcset` 的口径、注入判据之外的载荷清单、真实规模数据、页面配色与分页、错误文案措辞都还没定。
