# XLSX → Verilog 生成器

`xlsx2verilog.py` 读取 Excel 中的模块定义和集成关系，为每个模块生成一个 `.v` 文件。脚本只依赖 Python 3.10+ 标准库，目标机器不需要联网，也不需要安装 `openpyxl`。

## 快速使用

```powershell
# 指定输入和输出目录
python .\xlsx2verilog.py .\test.xlsx -o .\generated

# 无参数且在交互式终端中运行时，进入终端菜单
python .\xlsx2verilog.py

# 只查看识别结果
python .\xlsx2verilog.py .\test.xlsx --list

# 只校验，不写文件
python .\xlsx2verilog.py .\test.xlsx --check

# 将位宽不匹配等警告也视为失败
python .\xlsx2verilog.py .\test.xlsx --check --strict
```

命令行参数仍全部保留。无参数并且标准输入、输出均为交互式终端时，脚本会启动终端 GUI 菜单；使用 `↑`、`↓` 移动选项，按 `Enter` 确认，按 `Esc` 返回上级菜单或退出。主菜单包含“生成”“查看”“校验”“严格校验”和“退出”，可继续选择 XLSX 和输出目录。显式传入 XLSX 或 `--list`、`--check`、`--strict` 等参数时直接按命令行模式执行，不显示菜单；管道、CI 等非交互环境维持原有命令行行为。

正常生成的返回码为 `0`，校验失败为 `2`。文件以 UTF-8 和 LF 换行写入；已有同名 `.v` 会被原子替换，输出目录内其他文件不会被删除。

## XLSX 规则

脚本按表头识别内容，不要求模块端口从固定行开始。空行和合并的“分类”单元格不影响解析。

### 模块定义页签

每个模块占一个页签，必须包含 `端口名`、`位宽`、`数值` 和 `i/o` 表头；数组相关表头可选：

| 表头 | 规则 |
|---|---|
| `端口名` | 合法的 Verilog 标识符；同一模块中的重复名称合并为同一个物理端口 |
| `位宽` | packed 位宽；支持正整数、安全整数表达式、宏（如 `` `DFT_BUS``）、parameter（如 `DATA_WIDTH`）、带空格 ` * ` 分隔的多维 packed array，或 interface modport（如 `sky_cs_if.mst`） |
| `数值` | 位宽宏或 parameter 的默认值；支持整数表达式；位宽为空时 `1` 表示标量 |
| `i/o` | 支持 `i/o/io`、`input/output/inout`、`输入/输出/双向`；interface 行使用 `NA`/`N/A`/`interface`；单元格为空时暂按 `inout` 生成，并在代码中加入 TODO 备注 |
| `数组`（可选） | 端口名之后的 unpacked array 深度，可用带空格的 ` * ` 表示多维；别名为 `数组维度`、`数组深度`、`array`、`depth` |
| `数组数值`（可选） | 数组深度宏或 parameter 的默认值；别名为 `数组默认值`、`arrayvalue`、`array_default`、`depthdefault`、`depth_default` |

模块名取自 `端口名` 表头上方同一列最近的非空单元格；找不到时使用页签名。

- 宏位宽会生成带保护的 `` `define``，例如 `` `DFT_BUS = 64``。
- 非反引号符号位宽会生成模块 parameter。
- 数字位宽 `N` 会生成 `[N-1:0]`；数字 `1` 生成标量。
- 重复名称可以出现在多行或不同模块中。单个 Verilog 模块只声明一次同名端口；同一模块后续同名行合并到第一次定义。
- 普通模块页签中的所有 output 会在模块内部连续赋零，方便生成结果直接通过基础语法检查。

### 命名模板端口（`{{i}}`、`{{j}}`、`{{z}}` 等）

`端口名`、`位宽`和数组维度中可使用任意合法变量名的 `{{变量名}}`，不再限定为 `i`。同一行的任意备注单元格中用单层花括号给出对应取值，如 `j的范围是{sig1,sig2,sig3}` 或 `i是{a,b}`；也支持 `i为{...}`、`i={...}`、`i:{...}` 和 `i in {...}`。变量列表会向同一“分类”的后续空分类行继承。例如：

```text
端口名: test_bus_{{i}}_dat
位宽:   `DW_{{i}}
备注:    i的列表是{sig1,sig2,sig3}
```

会展开为 `test_bus_sig1_dat`、`test_bus_sig2_dat`和 `test_bus_sig3_dat`，对应宏为 `` `DW_sig1``～`` `DW_sig3``。若模板宏/parameter 的`数值`无法可靠计算，脚本输出 warning 并使用 `114` 作为明确的待确认占位值。集成页签中保留原始 `{{i}}` 写法即可，脚本会按模块已展开端口逐项连接。

变量按名称绑定：`{{j}}` 只使用 `j的范围是{...}`，不会自动套用 `i` 的列表。同一端口名包含多个变量时采用笛卡尔积，例如 `bus_{{j}}_{{z}}` 配合 `j={a,b}`、`z={x,y}` 会生成 `bus_a_x`、`bus_a_y`、`bus_b_x`、`bus_b_y`。如果位宽或数组维度引用了未被端口名绑定的其他变量，脚本不会猜测变量之间的关系，而是 warning 后使用 `114`；如果端口名本身的变量没有取值列表，则报错且不生成该行。

集成页签中的模板引用按“来源模板 + 展开取值”匹配，不按最终端口名做前缀通配。例如 `data_{{i}}` 只匹配由该模板生成的端口，不会误匹配普通端口 `data_debug`。不同模块的取值顺序可以不同，脚本按值连接；取值集合确实不同时才报告展开不一致，并在错误中列出各模块实际匹配的端口。

对于可明确恢复的单花括号拼写错误（例如 `{j}}` 或 `{{j}`），脚本会按 `{{j}}` 处理并告警，XLSX 仍应修正。`i/o` 空白是另一类待确认数据：声明会生成为 `inout`，并附加 `/* TODO: XLSX i/o 为空...需处理方向缺失问题 */` 备注，同时输出 warning；无法识别的非空方向仍然报错。

### 表达式和多维数组

- 纯数字整数表达式会安全计算；支持括号、`+ - * / // % << >> & | ^`，不执行名称、函数或 Python 代码。例如 `2+3*4` 计算为 14 bit。
- `*`前后没有空格时是算术乘法，例如 `3*100` 计算为 300。
- `*`前后都有空格时表示维度分隔；`位宽` `A * B` 按原顺序生成多维 packed array `[A-1:0][B-1:0] port`。`数组`列中的 `A * B` 仍生成端口名之后的两个 unpacked 维度。
- 维度是宏/parameter 时，`数值`使用乘法表达式按位对应，如 `` `LANE_NUM * `Test_size`` 配合 `3*100`。

`位宽` 描述每个元素的 packed width，`数组` 描述端口名之后的第二维 unpacked depth。例如 `DATA_WIDTH` 的数值为 `32`、`DEPTH` 的数组数值为 `4` 时生成：

```systemverilog
input wire [DATA_WIDTH-1:0] data [DEPTH-1:0]
```

未连接的数组 input 使用 `'{default:'0}` 接零；普通模块及未驱动 output 的数组桩使用嵌套 `generate for` 逐元素赋零。多维端口、数组赋值和 interface 均属于 SystemVerilog 语法：生成文件仍使用 `.v` 扩展名，仿真、综合和 lint 工具必须显式启用 SystemVerilog 模式。

### Interface

当`位宽`是 `interface_type.modport`（如 `sky_cs_if.mst`）且 `i/o` 为 `NA`时，生成 `sky_cs_if.mst chi_if_risc`形式的 SystemVerilog interface 端口。集成页签同样使用 `NA`，实例按名连接。生成器不生成 interface 本身的定义，编译时需由项目提供。

## 生成代码格式

生成器会按当前模块中最长的字段统一排版：模块端口声明的方向、类型、packed 位宽和端口名按列对齐；模块实例的参数连接与端口连接中，左括号纵向对齐。该格式只改善可读性，不改变端口顺序或连接语义。

### 集成页签

同一行出现至少两组相邻的 `端口名`、`i/o` 表头时，该页签被识别为集成页签。每组的模块名位于 `端口名` 上方。

连接区由一个或多个空列隔开：

1. 第一个连续区是 `TOP ↔ 子模块`。第一组是 TOP，后续组是子模块；同一行的端口互连。
2. 后续包含至少两组模块的区是 `子模块 ↔ 子模块`。同一行生成一根内部 wire，wire 位宽取唯一 output 驱动端。
3. 后续只包含一组模块的区是“未连接端口”。子模块 input 接零，output/inout 使用空连接 `.port()`。
4. 集成表中完全遗漏的子模块端口也会按未连接端口处理，并产生警告。
5. 未由子模块 output/inout 驱动的 TOP output 会在 TOP 内赋零；该 TOP output 可以同时连接一个或多个子模块 input，并以置零后的同一信号驱动它们。TOP input 始终只作为外部输入，不在模块内赋值。

列位置可以扩展，不限于示例中的 B～Y；关键是每个区内的模块组相邻、不同区之间至少留一个空列。脚本同时检查集成页签的 `i/o` 与模块定义是否一致、多驱动、缺失端口及位宽差异。

任意页签的表头行中若存在名为 `修改` 或 `修改列` 的列，读取 XLSX 时会先整列移除，再执行任何表头、模板、分类、方向和连接解析。该列可以插在业务列之间，其中所有内容均不会影响生成结果。

### 方向和模板报错排查

- `方向与模块定义不一致 (input/output)`：括号左侧是集成页签该模块列的 `i/o`，右侧是模块子页的定义；先按错误中的页签、行号和 `模块.端口` 核对这两个单元格。
- `TOP 输入 ... 与子模块输出 ... 方向冲突`：表示 TOP 的外部 input 被子模块 output 反向驱动。通常 TOP input 应连接子模块 input；若信号应由子模块驱动到芯片外部，TOP 方向应为 output。
- `模板端口展开数量/取值不一致`：检查错误列出的实际端口，以及各模块备注中的变量名、值集合和所属“分类”。`i是{a,b}` 与 `i是{b,a}` 允许顺序不同；缺值、多值或拼写不同才是不一致。
- 排查时先运行 `python .\xlsx2verilog.py 文件.xlsx --check`，保留完整的第一条 error。前面的解析错误可能造成后续连接错误，不应只看最后一条诊断。

## 当前样例的说明

`test.xlsx` 可生成：

- `RISCV_TOP.v`：TOP 端口、APB 内部 wire、`RISCV_CORE_TEST` 和 `MEM_PHY` 实例；
- `RISCV_CORE_TEST.v`、`MEM_PHY.v`：端口定义及 output 赋零。

最新样例还包含 `test_bus_{{i}}_*` 和 `test_bus2_{{j}}_*` 的 `sig1/sig2/sig3` 展开、`sky_cs_if.mst` interface，以及 `` `LANE_NUM * `Test_size`` 数组；后者生成 ``[`LANE_NUM-1:0][`Test_size-1:0] array``。`DW_sig1`～`DW_sig3` 的 XLSX 默认值为不完整的 `155、…`，因此按 Tech Review 2 规则告警并使用 `114`。

新增 `test_bus2` 样例刻意保留了两个表格问题用于验证边界：`dat` 的端口名使用 `j`、位宽却引用未绑定的 `i`，因此三个端口均生成 `[113:0]` 并告警；`valid` 写成 `{j}}`，脚本恢复为 `{{j}}` 后展开并要求修正 XLSX。这些 warning 是有意保留的待确认标记。

集成表中的三个 `test_bus_*_valid` 同时连接 `RISCV_CORE_TEST` 和 `MEM_PHY` 的 output，因此顶层 net 存在多驱动风险。脚本按表格生成并输出“多个子模块驱动端” warning；请在项目定义确定后修改 XLSX 方向或连接关系。

样例中两个子模块各有一个重复的 `ahb_test_5`，脚本按允许重复的规则合并到首次定义；APB 两端的数字位宽不同，脚本按驱动端位宽生成 wire，并输出如下形式的警告：

```text
warning[RISCV_CORE_TEST.apb_3信号和MEM_PHY.apb_3信号应该连接，但是其位宽不匹配]
```

修正 Excel 后可使用 `--strict` 作为自动化流水线的质量门禁。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m py_compile .\xlsx2verilog.py
python .\tests\run_review_matrix.py
python .\tests\run_tech_review2_review.py
python .\xlsx2verilog.py .\review_test_cases\07_real_test_1\ibex_if_stage_3children.xlsx --check --strict
python .\xlsx2verilog.py .\review_test_cases\08_real_test_2\01_core_layer.xlsx --check --strict
python .\xlsx2verilog.py .\review_test_cases\08_real_test_2\02_if_stage_layer.xlsx --check --strict
```

测试同样只使用标准库。除直接使用仓库中的 `test.xlsx` 外，`run_review_matrix.py` 还会创建 6 种不同结构的 XLSX，逐一调用本工具、立即静态检视生成结果，并在 `review_test_cases/检视报告.md` 中将发现归类。`run_tech_review2_review.py` 会顺序执行新功能、历史回归、生成 Verilog 静态结构三轮检视，并写入 `review_test_cases/TechReview2检视报告.md`。
