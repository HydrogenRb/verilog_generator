# XLSX → Verilog 生成器

`xlsx2verilog.py`把 XLSX 中的模块定义和集成关系生成 Verilog/SystemVerilog 模块桩与 TOP。当前版本为 **V3.5.05**，仅依赖 Python 3.10+ 标准库，不需要 `openpyxl`，适合离线环境。

已有 RTL 工程接收新生成结果时，使用独立工具 [`appendix/xlsx2verilog_merger.py`](appendix/README.md)。主生成器和 merger 是两个入口：前者以 XLSX 生成新结构，后者把受保护的人工内容迁移到新结构。

## 文档入口

| 需要了解的内容 | 文档 |
|---|---|
| 当前全部功能 | [功能特性表](doc/TechReport/功能特性表.md) |
| 位宽、维度、方向、宏和 parameter 的实际裁决 | [V3.5.03 位宽行为矩阵](doc/TechReport/V3.5.03位宽行为矩阵.md) |
| TOP 正文 localparam 的所有来源 | [V3.5.03 parameter / localparam 生成规则](doc/TechReport/V3.5.03局部参数生成规则.md) |
| 已知 bug 风险、触发条件和建议测试 | [V3.5.03 代码风险检视](doc/TechReport/V3.5.03代码风险检视.md) |
| 已下线需求与后续瘦身顺序 | [V3.5.03 代码瘦身规划](doc/TechReport/V3.5.03代码瘦身规划.md) |
| 内部结构和修改入口 | [代码结构与维护指南](doc/TechReport/代码结构与维护指南.md) |
| merger 的用法与保护范围 | [appendix README](appendix/README.md) |

历史 PRD 和历史 TechReport 用于追溯版本，不自动代表当前行为；遇到冲突时，以当前代码、测试和当前 README 为准。表中的 V3.5.03 审计文档仍是本版继承的基线分析。

## 快速开始

```powershell
# 生成指定集成 TOP 及其子模块
python .\xlsx2verilog.py .\main_test.xlsx --integration 集成_RISCV_TOP -o .\generated

# 只校验，不写文件
python .\xlsx2verilog.py .\test.xlsx --check

# warning 也视为失败
python .\xlsx2verilog.py .\test.xlsx --check --strict

# 查看识别出的模块、集成页签和输出目标
python .\xlsx2verilog.py .\test.xlsx --list

# 交互式 terminal 菜单
python .\xlsx2verilog.py
```

无参数且 stdin/stdout 均为交互式终端时，菜单提供“生成、查看、校验、严格校验、退出”。工作簿只有一个有效集成页签时自动选择；有多个时菜单要求选择，CLI 使用 `--integration SHEET`明确指定。

返回码：成功为 `0`，校验或生成失败为 `2`。`--check`不写 `.v`，但仍执行用户代码段完整性检查。

## 工作簿结构

一个工作簿可以包含：

- 模块定义页签：每页一个模块；
- 一个或多个集成页签：推荐命名为 `集成`或 `集成_xxx`；
- 可选 `define`页签：集中提供宏的匹配值，不生成活动 `` `define``。

任意页签表头中的 `修改`或 `修改列`会在所有业务解析前整列忽略。

### 模块定义页签

模块页按表头定位，不要求固定行号。必需表头为 `端口名`、`位宽`、`数值`、`i/o`；数组列可选。

| 表头 | 用途 |
|---|---|
| `端口名` | Verilog 端口名、parameter 名或宏名 |
| `位宽` | literal、宏、parameter、多维 packed 因子或 interface modport |
| `数值` | 宏/parameter 的静态匹配默认值 |
| `i/o` | `i/input/输入`、`o/output/输出`、`io/i/o/inout/双向`；interface 使用 `NA` |
| `数组` | 外层维度；别名包括 `数组维度`、`数组深度`、`array`、`depth` |
| `数组数值` | 数组维度匹配值；支持相应中英文别名 |

模块名取 `端口名`表头上方同列最近的非空单元格，找不到时使用页签名。模块名与 parameter 名转为大写；普通信号、端口、自定义实例名和非模板宏保持 XLSX 大小写。模板展开产生的宏名强制大写。

模块页 `i/o`空白时暂按 `inout`声明，同时生成 TODO 和 `W_IO_DEFAULTED`。无法识别的非空方向是 error。

分类列有三种特殊用途：

| 分类 | 行的含义 |
|---|---|
| 普通文本 | 端口分组注释 |
| `parameter` / `parameters` / `参数` / `参数定义` | 模块参数声明，不是端口 |
| `宏定义` / `宏` / `macro` / `macros` | 只登记宏匹配值，不生成活动定义 |

分类会向后续空分类单元格继承。备注包含 `*注释*`时，该 XLSX 行完全不参与模型和生成，并输出 `I_ROW_COMMENTED`。

### 位宽与多维

顶层 `*`表示维度分隔，空格无语义差异：

| 位宽 | 数值 | 结果 |
|---|---|---|
| 空白或 `1` | 空白 | 标量 |
| `8` | 空白 | `[8 -1:0]` |
| `0` | 空白 | `[0 -1:0]`并报 `W_ZERO_WIDTH` |
| `(2+3*4)` | 空白 | 计算为 `[14 -1:0]` |
| `` `DATA_W`` | `32` | ``[`DATA_W -1:0]`` |
| `DATA_W` | `32` | `[DATA_W -1:0]`并声明 parameter |
| `LANE*DW` | `4*8` | `[LANE -1:0][DW -1:0]` |
| `TOTAL` | `(4*8)` | 单维 `[TOTAL -1:0]`，匹配值 32 |

所有普通信号维度都生成在名称左侧，顺序为“数组列各维 → 位宽列各维”。interface 实例数组受 SystemVerilog 语法限制，是名称右侧维度的唯一例外。

位宽和数值的顶层维度数必须一致。无法确定的模板匹配值可能用 `114`占位并 warning；生产 CI 应使用 `--strict`或单独阻断 `W_WIDTH_PLACEHOLDER`。

完整的连接适配规则见[位宽行为矩阵](doc/TechReport/V3.5.03位宽行为矩阵.md)。其中最重要的边界是：parameter 自动适配默认关闭；打开后多维 TOP rvalue、indexed 连接和多维内部网络仍有待补测风险。

### parameter 表达式

parameter 分类中：

- `位宽`留空：生成值取 `数值`；
- `位宽`非空：作为实际 Verilog parameter 表达式；
- `数值`继续用于生成期静态匹配和行尾注释；
- 允许数值 `0`；
- 支持完整宏、宏调用、其他 parameter、系统函数、数值字面量及常用运算符；
- parameter 标识符转大写，宏名和系统函数保持用户输入。

例如：

```text
parameter | para_a | `LANE_NUM     | 4
parameter | para_b | para_a+1      | 5
parameter | DW     | `log2(para_b) | 3
```

生成：

```systemverilog
parameter PARA_A = `LANE_NUM,    // 4
parameter PARA_B = PARA_A+1,     // 5
parameter DW     = `log2(PARA_B) // 3
```

模块头中一律为可覆盖的 `parameter`。只有 TOP 正文中为 NA、未链接子模块宽度或数字映射创建的内部常量使用 `localparam`。详细触发矩阵见[局部参数生成规则](doc/TechReport/V3.5.03局部参数生成规则.md)。

### 模板端口

端口名、位宽和数组维度可使用任意合法变量 `{{i}}`、`{{j}}`、`{{z}}`等。备注中提供值域：

```text
i是{a,b,c}
z=0:31
j in {left,right}
```

同一端口含多个变量时做笛卡尔积。变量按名称绑定，不会把 `j`的值域自动给 `i`。模板信号名和值保持大小写；只有模板宏展开结果强制大写。集成页可继续写模板原式，脚本按模板来源和值逐项对齐，而不是对最终端口名做前缀通配。

可明确恢复的单花括号错误会修复并报 `W_TEMPLATE_REPAIR`；端口名模板变量没有值域则是 error。单个闭区间最多展开 4096 项。

### define 页签

唯一的 `define`页签可用下列表头集中维护宏匹配值：

- 名称：`宏名`、`名称`、`name`、`macro`、`define`或`端口名`；
- 值：`数值`、`value`、`default`或`默认值`。

它只为解析、冲突检查和位宽匹配提供值，不向 RTL 写入活动 `` `define``。真实宏仍由项目编译环境提供。

## 集成页签

命名页签优先按 `集成` / `集成_xxx`发现；没有命名候选时保留历史结构识别。有效集成页同一行至少包含两组相邻的 `端口名`、`i/o`表头。模块标签可写：

```text
RISCV_CORE
module:RISCV_CORE
module:RISCV_CORE 例化名:CORE0
module:RISCV_CORE *注释*
```

自定义例化名优先于右侧元数据表。同一 module 使用不同例化名时形成独立实例，但模块定义文件只生成一次。`*注释*`子模块仍完成解析与连线，只在输出中整体注释实例；TOP 不允许停用。

第一连接区描述 TOP 与子模块。其余连接区统一按真实端点数量处理：

- 一个真实 input：接完整形状 0；
- 一个真实 output/inout：开路；
- 两个或更多真实端点：创建内部 wire；
- 唯一 output 是驱动源；无 output 报 `W_DRIVER_RISK`；多个 output 报 `E_DRIVER_CONFLICT`。

TOP input 连接 child output 是 `E_DIRECTION`。TOP output 连接 child input 是允许的：若没有 child 驱动 TOP，脚本把 TOP output 置 0，再由该信号驱动 child input。

### NA 功能

| 写法 | 普通端口含义 |
|---|---|
| `NA` | 创建自动命名占位，或按单端未连接处理 |
| `NA[i]` | 占位并参与 generate 索引 |
| `NA->name` | 创建/复用指定名称的观察或占位 wire |
| `NA->0` / `NA->1` | 按目标全部 packed 维度生成全 0 / 全 1 |
| `NA->8'hFF` | 定宽常量；较窄补 0，过宽 warning 后由上下文截断 |

第一 TOP 区和后续内部连接区均支持 `NA->name`。名称已被 TOP 端口或内部 wire 使用时直接复用并输出 `W_NA_TARGET_CONFLICT`，不会自动解决方向、形状和多驱动问题。interface TOP 端口不支持普通连续赋值观察。

parameter 分类中的 `NA->A`、`NA->514`、`NA[i]->B`会在 TOP 正文创建 localparam并传给子模块；规则与冲突处理见[局部参数生成规则](doc/TechReport/V3.5.03局部参数生成规则.md)。

### V3.5.05 子模块端口切片

后续子模块内部连接区支持在端口名末尾选择最左侧 packed 维度：

```text
tx_fifo[5:3]
tx_fifo[3+:3]
tx_fifo[2]
```

`[3+:3]`会规范输出为 `[3 +: 3]`，与 `[5:3]`选择相同的三个元素。显式范围必须按生成声明的降序 `[msb:lsb]`书写；`[0:2]`会报 `E_PORT_SLICE`。

同一个 output 端口可以在多行使用不同切片。脚本只为它创建一根完整内部 wire、只生成一次实例端口绑定，再把各切片连接给不同 input。例如 OE 为 `[6][2][2]`、两个 LC 为 `[3][2][2]`时：

```text
LC_A.tx_fifo  <-> OE.tx_fifo[2:0]
LC_B.tx_fifo  <-> OE.tx_fifo[5:3]
```

生成结构为：

```systemverilog
wire [6 -1:0][2 -1:0][2 -1:0] w_tx_fifo;

LC LC_A (
    .tx_fifo (w_tx_fifo[2:0])
);
LC LC_B (
    .tx_fifo (w_tx_fifo[5:3])
);
OE U_OE (
    .tx_fifo (w_tx_fifo)
);
```

输入端和输出端可以同时写范围。同一个 input 端口分散在多行时，脚本按目标下标从高到低拼成一个完整表达式；未覆盖区间和显式 `NA->0`都按该段剩余 packed 维度生成定宽全 0：

```text
LC.tx_fifo[1:0] <-> OE.tx_fifo[4:3]
LC.tx_fifo[2]   <-> NA->0
```

生成：

```systemverilog
.tx_fifo ({{2*2{1'b0}}, w_tx_fifo[4:3]})
```

切片两端总位宽不同时，源窄则在端口表达式中补定宽 0；源宽则先写入等宽的一维 adapter，再安全截取低位。这样不会把一个已知不等宽表达式直接交给 VCS。该处理是显式切片语义的一部分，不受 parameter 位宽自动适配总开关影响。若最左维来自 parameter 或宏，边界按 XLSX 数值列/当前宏匹配值检查，并输出`W_PORT_SLICE_WIDTH`；之后若修改实例传参或编译宏，必须保证选择范围仍未越界且总宽度仍一致。

当前切片限制：仅用于第一个 TOP 连接区之后的子模块内部连接区；只支持自然数范围；作用于最左 packed 维；驱动端必须是唯一 output、接收端必须是 input；暂不与 interface、inout、条件端口、模板展开或 `[i]` generate 标记混用。目标切片重叠、范围反向或越界会阻止写入。

## 文件顶部配置

配置集中在 `xlsx2verilog.py`开头的 `User configuration`：

```python
ENABLE_CONDITIONAL_BLOCKS = False
ONLY_TOP = False
AUTO_ZERO_FILL_PARAMETER_WIDTH_MISMATCH = False
OVERWRITE_FILE_HEADER = False

SHOW_ERROR_MESSAGES = True
SHOW_WARNING_MESSAGES = True
SHOW_INFO_MESSAGES = True

DIAGNOSTIC_VISIBILITY_BY_CODE = {
    "E_DIRECTION": True,
    "W_WIDTH_MISMATCH": True,
    "W_PARAMETER_WIDTH_MISMATCH": True,
    "I_UNCONNECTED": False,
    # 完整表见脚本开头
}
```

| 配置 | 作用 |
|---|---|
| `ENABLE_CONDITIONAL_BLOCKS` | 默认不生成 `ifdef`；设为 True 后按分类条件同步包裹声明、stub assign、实例连接和 TOP 兜底 |
| `ONLY_TOP` | 仍解析、校验完整层次，但只写所选 TOP |
| `AUTO_ZERO_FILL_PARAMETER_WIDTH_MISMATCH` | 默认只 warning；开启后按 XLSX 匹配值自动低位连接/补零 |
| `OVERWRITE_FILE_HEADER` | False 保留已有 file header USER 内容；True 用配置头覆盖 |
| `SHOW_*` | 诊断级别显示总开关 |
| `DIAGNOSTIC_VISIBILITY_BY_CODE` | 每个诊断代码的细粒度显示开关 |

诊断显示开关只影响终端，不改变内部记录、返回码、error 写入保护或 `--strict`判定。

## 输出与用户代码段

输出文件名统一小写，模块名保持模型中的大小写。文件使用 UTF-8、LF，写入采用临时文件替换；输出目录中的无关文件不会删除。

每个生成文件有稳定 USER CODE 段：

- `file header`；
- `before statement`；
- 自动 statement 后、首个实例或 `endmodule`前的 `after statement`；
- TOP module 前的 `before module`；
- 每个实例前后的 `before <identity>` / `after <identity>`。

只编辑 BEGIN/END 中间内容，不要修改、删除或嵌套 marker。重新生成按“标签 + occurrence”保留段内容；损坏 marker 或有内容的旧段失去对应位置时，在任何文件写入前失败。

## merger

典型用法见 [appendix README](appendix/README.md)。merger 以新生成文件为结构真源，并保护：

- USER CODE 段；
- 同模块、同 scope、同信号的 `wire/reg`选择；
- 同模块、同参数的 `localparam/parameter`选择；
- 带 `//USER:`的完整单行 assign、声明、parameter、named-port connection；
- 带 `//USER:`的 `genvar/generate/endgenerate`。

merger 对旧工程先备份，再暂存和原子替换；失败会回滚。但它不是完整 SystemVerilog parser，`//USER:`保护只承诺文档列出的单行形式。人工 assign 被完整保留后仍需项目编译/lint验证。重复 USER 标签的 occurrence 匹配等边界见[代码风险检视](doc/TechReport/V3.5.03代码风险检视.md)。

## 诊断与排查

推荐顺序：

1. 先运行普通 `--check`并处理第一条 error；
2. 再处理 width、placeholder、driver、NA 等 warning；
3. 最后运行 `--check --strict`作为质量门禁；
4. 对打开自动适配、interface、多维或 parameter override 的工程执行真实 SystemVerilog compile/lint。

常见诊断：

| 代码 | 含义 |
|---|---|
| `E_DIRECTION` | TOP input 被 child output 反向驱动，或方向不合法 |
| `E_PARAMETER` | parameter 行来源、表达式或链接冲突 |
| `E_DRIVER_CONFLICT` | 内部网络有多个 output 驱动 |
| `W_WIDTH_MISMATCH` | literal/macro 等普通形状不一致 |
| `W_PARAMETER_WIDTH_MISMATCH` | 含 parameter 的形状不一致 |
| `W_WIDTH_PLACEHOLDER` | 使用了 114 待确认宽度 |
| `W_ZERO_WIDTH` | 保留了显式零位宽 |
| `W_PARAMETER_AUTO_LOCAL` | 未链接的 child 宽度 parameter 已在 TOP 正文局部化 |
| `W_NA_TARGET_CONFLICT` | 命名 NA 复用了已有信号，需人工检查方向/驱动 |

## 测试

```powershell
# 全量单元测试
python -m unittest discover -s tests_script -v

# 当前宽度、端口切片、parameter 与 merger 定向回归
python -m unittest tests_script.test_version350 tests_script.test_version3505 tests_script.test_version3_review4 tests_script.test_version3_bugfix345 tests_script.test_merger_v350 -v

# Python 语法检查
python -m py_compile .\xlsx2verilog.py .\appendix\xlsx2verilog_merger.py

# 结构化 review matrix
python .\tests_script\run_review_matrix.py
python .\tests_script\run_tech_review2_review.py

# V3.2/V3.5 位宽边界样例：普通 check 成功，strict 因已知 warning 失败
python .\xlsx2verilog.py .\review_test_cases\17_v3_techreview2_width_boundary\width_boundary.xlsx --check

# V3 主验收样例：普通 check 成功，strict 因已知 warning 失败
python .\xlsx2verilog.py .\review_test_cases\14_edge_case_test_problem\eage_case.xlsx --check
```

部分历史 workbook 刻意保留宏冲突，预期返回 2，不应当作成功样例：

- `review_test_cases/09_version_2/test.xlsx`；
- `review_test_cases/10_special_case/review2case.xlsx`；
- `review_test_cases/13_test_for_techreview3/techreview2version3.xlsx`。

V3.5.05 全量回归包含 119 个测试，其中 7 个历史可视化附录测试因组件未归档而跳过。新增切片测试覆盖双实例分流、`+:`、双边切片、bit 与 range 拼接、`NA->0`、自动补零、扁平截断、反向/零宽/越界和目标重叠。warning 数量会随规则演进，不应在 README 中长期写成固定验收规格，测试应优先断言诊断代码与具体行为。

## 归档

- 当前维护文档：`doc/TechReport/`；
- 历史实现检视：`doc/TechReport/design_review/`；
- 主生成器需求：`doc/PRD/`；
- merger 与其他附录需求：`doc/appendix需求/`；
- 跨集成同步的未来方案：[V3.5 跨集成同步与层级信号传播方案](doc/TechReport/V3.5跨集成同步与层级信号传播方案.md)。
