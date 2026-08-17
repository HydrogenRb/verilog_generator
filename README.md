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

## 文件顶部配置

条件编译块默认关闭。若项目需要根据分类中的`条件：MACRO`生成 `` `ifdef``，编辑 `xlsx2verilog.py` 开头的用户配置：

```python
ENABLE_CONDITIONAL_BLOCKS = True
```

保持默认的 `False` 时，`条件：MACRO`只用于整理分类名称，相关端口、assign 和实例连接全部按无条件内容生成；输出中不会出现由该功能产生的 `` `ifdef/`elsif/`else/`endif`` 或 `XLSX2VERILOG_INTERNAL_HAVE_CONNECTION_*`临时标记。该设置是脚本级配置，对一次运行中的所有页签和模块统一生效。

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

- 宏位宽会直接生成 `` `define``，例如 `` `define DFT_BUS 64``；按项目约定不添加 `` `ifndef/`endif`` 保护。
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

#### 位宽写法 specification

空格是位宽语义的一部分，尤其需要区分 `*` 与 ` * `：

| XLSX `位宽` | XLSX `数值` | 生成结果 | 说明 |
|---|---:|---|---|
| 空白或 `1` | 空白或 `1` | `input wire sig` | 标量 |
| `8` | 空白 | `input wire [7:0] sig` | 固定 8 bit 向量 |
| `2+3*4` | 空白 | `input wire [13:0] sig` | 纯数字表达式先计算为 14 |
| `` `DATA_W`` | `32` | `` `define DATA_W 32``，端口为 ``[`DATA_W-1:0]`` | 单个宏；宏定义直接输出，不加保护 |
| `DATA_WIDTH` | `32` | `parameter integer DATA_WIDTH = 32`，端口为 `[DATA_WIDTH-1:0]` | 单个 parameter |
| `` `LANE_NUM*DATA_WIDTH`` | `4*32` | `[127:0] sig` | `*` 两侧没有空格，按一个算术位宽计算为 128；不会保留两个符号，也不会由该行生成宏/parameter |
| `` `LANE_NUM * DATA_WIDTH`` | `4*32` | ``[`LANE_NUM-1:0][DATA_WIDTH-1:0] sig`` | ` * ` 两侧有空格，按两个 packed 维度解释；同时生成宏 `LANE_NUM=4` 和 parameter `DATA_WIDTH=32` |
| `DATA_WIDTH`，`数组=DEPTH` | `32`，`数组数值=4` | `input wire [DATA_WIDTH-1:0] sig [DEPTH-1:0]` | packed 元素宽度加 unpacked 数组深度 |
| `sky_bus_if.mst` | 空白 | `sky_bus_if.mst sig` | interface modport，不生成 `wire` |

复杂项目示例：若需要 4 lane、每 lane 32 bit 的二维 packed 端口，应写 `` `LANE_NUM * DATA_WIDTH``（乘号两侧有空格）和数值 `4*32`；若需要单根 128 bit 扁平总线，则写 `` `LANE_NUM*DATA_WIDTH``（无空格）和数值 `4*32`。无空格的符号表达式若没有可计算的纯数字默认值，会 warning 并使用 `[113:0]`，即 114 bit 待确认占位值；若希望稳定保留一个符号宽度，建议在表中使用单独的 `TOTAL_WIDTH` parameter 并把数值写为 `128`。

`位宽` 描述每个元素的 packed width，`数组` 描述端口名之后的第二维 unpacked depth。例如 `DATA_WIDTH` 的数值为 `32`、`DEPTH` 的数组数值为 `4` 时生成：

```systemverilog
input wire [DATA_WIDTH-1:0] data [DEPTH-1:0]
```

未连接的数组 input 使用 `'{default:'0}` 接零；普通模块及未驱动 output 的数组桩使用嵌套 `generate for` 逐元素赋零。多维端口、数组赋值和 interface 均属于 SystemVerilog 语法：生成文件仍使用 `.v` 扩展名，仿真、综合和 lint 工具必须显式启用 SystemVerilog 模式。

### Interface

当`位宽`是 `interface_type.modport`（如 `sky_cs_if.mst`）且 `i/o` 为 `NA`时，生成 `sky_cs_if.mst chi_if_risc`形式的 SystemVerilog interface 端口。interface 端口名与同一模块的普通 input/output/inout 端口名使用相同起始列。集成页签同样使用 `NA`，实例按名连接。生成器不生成 interface 本身的定义，编译时需由项目提供。

## 生成代码格式

生成器会按当前代码块中最长的字段统一排版：普通端口与 interface 的端口名按列对齐；packed/unpacked 范围从右侧对齐，使末尾右方括号、冒号及符号范围中的 `-1` 运算符纵向对齐；同一组宏定义的值、wire 名、assign 等号、localparam 等号分别对齐；模块实例的参数连接与端口连接中，左、右圆括号均纵向对齐。不同代码块不强求全局列宽。该格式只改善可读性，不改变端口顺序或连接语义。

```verilog
input wire   [`SHORT-1:0] short_bus,
input wire [`LONG_NAME-1:0] long_bus,
input wire            [7:0] literal_bus

.short_port (a_signal        ),
.long_port  (long_signal_name)
```

生成的 localparam、内部 wire 和自动 assign 代码块均同时输出英文与中文说明，便于中英文项目成员直接阅读生成文件。

模块端口按“分类”分组，每组生成三行标题。合并单元格在首行给出的分类会由后续连续端口继承；完全没有分类的连续端口使用 `no group`：

```verilog
    // ----- ----- ----- ----- ----- -----
    // CLK & RST
    // ----- ----- ----- ----- ----- -----
    input wire clk,
    input wire rst_n
```

分类中可加入 `条件：宏名`。例如 `CFG bus 条件：FEATURE_CFG` 显示分类名 `CFG bus`；仅写 `条件：FEATURE_CFG` 时，显示分类名就是 `FEATURE_CFG`。也接受 ``条件：`FEATURE_CFG``。默认配置不生成条件块。仅当文件顶部 `ENABLE_CONDITIONAL_BLOCKS = True` 时，整组端口才放入 `` `ifdef FEATURE_CFG``；条件会同步应用到端口声明、桩模块的 output 赋零以及集成模块的实例连接。条件宏由外部编译环境控制，生成器不会在文件开头创建对应 `` `define``。多个条件组关闭、单独开启或同时开启时，生成器都会按预处理分支放置分隔逗号。即使 CORE 的每个端口都有 `ifdef` 且当前没有任何条件开启，生成的空端口列表和空实例连接列表仍保持合法。条件化子模块 output/inout 驱动无条件或不同条件的 TOP output 时，驱动端存在的配置使用子模块输出，其余配置自动把 TOP output 置零，避免信号悬空。

### 集成页签

同一行出现至少两组相邻的 `端口名`、`i/o` 表头时，该页签被识别为集成页签。每组的模块名位于 `端口名` 上方，既可以写 `RISCV_CORE`，也可以写 `module:RISCV_CORE`、`module：RISCV_CORE`；前缀不进入生成的模块名和实例名。

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

测试同样只使用标准库。除直接使用仓库中的 `test.xlsx` 外，`run_review_matrix.py` 还会创建 6 种不同结构的 XLSX，逐一调用本工具、立即静态检视生成结果，并在 `review_test_cases/检视报告.md` 中将发现归类。`run_tech_review2_review.py` 会顺序执行新功能、历史回归、生成 Verilog 静态结构三轮检视，并写入 `review_test_cases/TechReview2检视报告.md`。Tech Review 3 的需求追踪、条件组合验证和整体代码审查记录见 `review_test_cases/TechReview3检视报告.md`；代码分层、关键不变量和常见修改入口见 `代码结构与维护指南.md`；后续可视化连线软件的独立 specification 见 `可视化集成页签生成器需求文档.md`。
