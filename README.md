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

# 一个工作簿有多个集成页签时，CLI 明确选择其中一个 TOP
python .\xlsx2verilog.py .\multi_top.xlsx --integration 集成_riscv_top -o .\generated

# 只校验，不写文件
python .\xlsx2verilog.py .\test.xlsx --check

# 将位宽不匹配等警告也视为失败
python .\xlsx2verilog.py .\test.xlsx --check --strict

# 扩散一个变量值（仍会要求输入 y/n；确认后原地修改 XLSX）
python .\xlsx2verilog.py .\design.xlsx --spread-value WIDTH "(3+5)"
```

命令行参数仍全部保留。无参数并且标准输入、输出均为交互式终端时，脚本会启动终端 GUI 菜单；使用 `↑`、`↓` 移动选项，按 `Enter` 确认，按 `Esc` 返回上级菜单或退出。主菜单包含“生成”“查看”“校验”“严格校验”“扩散变量值”和“退出”，可继续选择 XLSX 和输出目录。工作簿中有多个有效的集成页签时，菜单会再列出“页签名 → TOP 模块名”供选择；只有一个`集成`或`集成_xxx`页签时自动使用，不显示这一级菜单。显式传入 XLSX 或 `--list`、`--check`、`--strict` 等参数时直接按命令行模式执行，不显示菜单；多集成页签工作簿必须通过`--integration 页签名`消除歧义，管道、CI 等非交互环境不会等待输入。修改 XLSX 的 `--spread-value` 是唯一例外：无论从菜单还是 CLI 进入，都必须再次输入 `y` 才会修改。

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
| `位宽` | packed 位宽；支持正整数、整体带括号的安全整数表达式、宏（如 `` `DFT_BUS``）、parameter（如 `DATA_WIDTH`）、由任意顶层 `*` 分隔的多维 packed array，或 interface modport（如 `sky_cs_if.mst`） |
| `数值` | 位宽宏或 parameter 的默认值；支持整数表达式；位宽为空时 `1` 表示标量 |
| `i/o` | 支持 `i/o/io`、`input/output/inout`、`输入/输出/双向`；interface 行使用 `NA`/`N/A`/`interface`；单元格为空时暂按 `inout` 生成，并在代码中加入 TODO 备注 |
| `数组`（可选） | 端口名之后的 unpacked array 深度；任意顶层 `*` 表示多维；别名为 `数组维度`、`数组深度`、`array`、`depth` |
| `数组数值`（可选） | 数组深度宏或 parameter 的默认值；别名为 `数组默认值`、`arrayvalue`、`array_default`、`depthdefault`、`depth_default` |

模块名取自 `端口名` 表头上方同一列最近的非空单元格；找不到时使用页签名。Excel 中的页签名、模块名和端口名均不限制大小写；生成时模块名、实例名、宏和 parameter 规范为大写，所有普通信号/端口名规范为小写。规范后重名会报错。

- 宏位宽会直接生成 `` `define``，例如 `` `define DFT_BUS 64``；按项目约定不添加 `` `ifndef/`endif`` 保护。
- 非反引号符号位宽会生成模块 parameter。
- 数字位宽 `N` 会生成 `[N -1:0]`；数字 `1` 生成标量。
- 重复名称可以出现在多行或不同模块中。单个 Verilog 模块只声明一次同名端口；同一模块后续同名行合并到第一次定义。
- 普通模块页签中的所有 output 会在模块内部连续赋零，方便生成结果直接通过基础语法检查。

### 命名模板端口（`{{i}}`、`{{j}}`、`{{z}}` 等）

`端口名`、`位宽`和数组维度中可使用任意合法变量名的 `{{变量名}}`，不再限定为 `i`。同一行的任意备注单元格中用单层花括号给出对应取值，如 `j的范围是{sig1,sig2,sig3}` 或 `i是{a,b}`；也支持 `i为{...}`、`i={...}`、`i:{...}` 和 `i in {...}`。变量列表会向同一“分类”的后续空分类行继承。例如：

```text
端口名: test_bus_{{i}}_dat
位宽:   `DW_{{i}}
备注:    i的列表是{sig1,sig2,sig3}
```

会展开为 `test_bus_sig1_dat`、`test_bus_sig2_dat`和 `test_bus_sig3_dat`，对应宏为 `` `DW_SIG1``～`` `DW_SIG3``。模板生成的最终信号标识符统一转换为小写，展开后的反引号宏标识符统一转换为大写；模板取值本身仍用于跨模块逐项对齐。若模板宏/parameter 的`数值`无法可靠计算，脚本输出 warning 并使用 `114` 作为明确的待确认占位值。集成页签中保留原始 `{{i}}` 写法即可，脚本会按模块已展开端口逐项连接。

变量按名称绑定：`{{j}}` 只使用 `j的范围是{...}`，不会自动套用 `i` 的列表。同一端口名包含多个变量时采用笛卡尔积，例如 `bus_{{j}}_{{z}}` 配合 `j={a,b}`、`z={x,y}` 会生成 `bus_a_x`、`bus_a_y`、`bus_b_x`、`bus_b_y`。如果位宽或数组维度引用了未被端口名绑定的其他变量，脚本不会猜测变量之间的关系，而是 warning 后使用 `114`；如果端口名本身的变量没有取值列表，则报错且不生成该行。

集成页签中的模板引用按“来源模板 + 展开取值”匹配，不按最终端口名做前缀通配。例如 `data_{{i}}` 只匹配由该模板生成的端口，不会误匹配普通端口 `data_debug`。不同模块的取值顺序可以不同，脚本按值连接。V2 允许各模块展开集合不同：共有取值正常互连，仅存在于 TOP 或某个子模块的展开项按单端/未连接语义处理，并输出 `info` 说明，不再因数量或取值集合不同而中止生成。

对于可明确恢复的单花括号拼写错误（例如 `{j}}` 或 `{{j}`），脚本会按 `{{j}}` 处理并告警，XLSX 仍应修正。`i/o` 空白是另一类待确认数据：声明会生成为 `inout`，并附加 `/* TODO: XLSX i/o 为空...需处理方向缺失问题 */` 备注，同时输出 warning；无法识别的非空方向仍然报错。

### 变量值扩散（会修改 XLSX）

终端菜单的“扩散变量值”或 CLI 的 `--spread-value VARIABLE VALUE` 一次只处理一个宏/parameter。脚本先执行完整解析并展示现有 error/warning，然后要求输入 `y` 或 `n`；只有 `y` 会继续。确认后，原文件先复制到同级 `backup/`，文件名带微秒级时间戳，再通过临时 ZIP 原子替换当前 XLSX。输入 `n` 不修改文件，也不创建备份。

扩散值允许正自然数或整体带括号的安全整数表达式，例如 `8`、`(3+5)`；不接受未加外层括号的 `3+5`。普通单维直接覆盖`数值`；多维只覆盖变量所在因子，其他空因子使用可计算的数字位宽，否则填 `114`。模板变量会写成显式范围，例如把 `BUS_REQ` 扩散为 `(3+5)` 后可得到：

```text
位宽: BUS_{{z}}*DW
数值: 范围是{114,(3+5),114}*8
备注: z的范围是{dat,req,rsp}
```

`修改`/`修改列`在变量发现、范围解析和写回计划中同样完全忽略。`temp_test/variable_diffusion_before.xlsx` 与 `variable_diffusion_after.xlsx` 分别提供扩散前 error 和扩散后无 error 的可复验样例。

### 表达式和多维数组

- 顶层 `*` 一律表示维度分隔，乘号前后空格没有语义差异。`A*B`、`A * B` 都生成 `[A -1:0][B -1:0]`。
- 若 `*` 是算术表达式的一部分，必须把整个表达式放进括号，例如 `(3*5)` 计算为单维 15。安全计算支持括号、`+ - * / // % << >> & | ^`，不会执行名称、函数或 Python 代码。
- `位宽`和`数值`的顶层 `*` 数量必须一致：`LANE_NUM*LANE_W` 可配 `3*5` 或 `(1+2)*5`，但不能配单值 `15`；单维 `LANE_NUM` 不能配 `3*5`，可以配 `(3*5)`。
- 模板维度的具体默认值用 `范围是{1,2,3}` 声明，例如 `BUS_OUT_{{z}}*DW` 可配 `范围是{32,32,64}*8`。

#### 位宽写法 specification

空格不再影响 `*` 的含义；只有括号能把乘法保留为单维算术：

| XLSX `位宽` | XLSX `数值` | 生成结果 | 说明 |
|---|---:|---|---|
| 空白或 `1` | 空白或 `1` | `input wire sig` | 标量 |
| `8` | 空白 | `input wire [8 -1:0] sig` | 固定 8 bit 向量；对齐补白位于 `8` 与 `-1:0]` 之间 |
| `(2+3*4)` | 空白 | `input wire [14 -1:0] sig` | 括号内表达式先计算为 14 |
| `` `DATA_W`` | `32` | `` `define DATA_W 32``，端口为 ``[`DATA_W -1:0]`` | 单个宏；宏定义直接输出，不加保护 |
| `DATA_WIDTH` | `32` | `parameter integer DATA_WIDTH = 32`，端口为 `[DATA_WIDTH -1:0]` | 单个 parameter |
| `` `LANE_NUM*DATA_WIDTH`` | `4*32` | ``[`LANE_NUM -1:0][DATA_WIDTH -1:0] sig`` | 无论乘号是否带空格，都按两个 packed 维度解释 |
| `TOTAL_WIDTH` | `(4*32)` | `[TOTAL_WIDTH -1:0] sig` | 括号使默认值保持单维，计算为 128 |
| `DATA_WIDTH`，`数组=DEPTH` | `32`，`数组数值=4` | `input wire [DATA_WIDTH -1:0] sig [DEPTH -1:0]` | packed 元素宽度加 unpacked 数组深度 |
| `sky_bus_if.mst` | 空白 | `sky_bus_if.mst sig` | interface modport，不生成 `wire` |

复杂项目示例：若需要 4 lane、每 lane 32 bit 的二维 packed 端口，应写 `` `LANE_NUM*DATA_WIDTH`` 和数值 `4*32`。若需要单根 128 bit 扁平总线，应使用单独的 `TOTAL_WIDTH`，数值写 `128` 或 `(4*32)`。不确定的模板位宽会 warning 并使用 `[114 -1:0]` 作为待确认占位值。

`位宽` 描述每个元素的 packed width，`数组` 描述端口名之后的第二维 unpacked depth。例如 `DATA_WIDTH` 的数值为 `32`、`DEPTH` 的数组数值为 `4` 时生成：

```systemverilog
input wire [DATA_WIDTH -1:0] data [DEPTH -1:0]
```

未连接的数组 input 使用 `'{default:'0}` 接零；普通模块及未驱动 output 的数组桩使用嵌套 `generate for` 逐元素赋零。多维端口、数组赋值和 interface 均属于 SystemVerilog 语法：生成文件仍使用 `.v` 扩展名，仿真、综合和 lint 工具必须显式启用 SystemVerilog 模式。

### Interface

当`位宽`是 `interface_type.modport`（如 `sky_cs_if.mst`）且 `i/o` 为 `NA`时，生成 `sky_cs_if.mst chi_if_risc`形式的 SystemVerilog interface 端口。interface 端口名与同一模块的普通 input/output/inout 端口名使用相同起始列。集成页签同样使用 `NA`，实例按名连接。生成器不生成 interface 本身的定义，编译时需由项目提供。

## 生成代码格式

生成器会按当前代码块中最长的字段统一排版：普通端口与 interface 的端口名按列对齐；packed 范围按维度建立独立列，每一维的左方括号固定，数字、参数名或宏名紧跟 `[` 并左对齐，所需空格放在表达式与减号之间，使该维的 `-1:0]` 仍然纵向对齐，第二维及后续维度使用相同规则；所有生成的 `-1:0` 前至少保留一个空格，便于 gvim 搜索。unpacked 范围的右方括号使用独立字段对齐；同一组宏定义的值、parameter 名、wire 名和 assign 等号分别对齐；模块实例声明从第 1 列开始，参数连接和端口连接只缩进 4 个空格，左、右圆括号均纵向对齐。模块级 `assign` 和对应说明注释从第 1 列开始，数组 generate 内部的 `assign` 仍按循环层级缩进。模块主体结束后直接生成 `endmodule`，不在其前方保留空白行。不同代码块不强求全局列宽。该格式只改善可读性，不改变端口顺序或连接语义。

```verilog
input wire [`SHORT     -1:0][`LANE    -1:0] matrix_a,
input wire [`LONG_NAME -1:0][`CHANNEL -1:0] matrix_b

CHILD U_CHILD (
    .short_port (a_signal        ),
    .long_port  (long_signal_name)
);
```

生成的内部 wire、位宽适配和自动 assign 代码块均同时输出英文与中文说明，便于中英文项目成员直接阅读生成文件。

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

页签名推荐使用`集成`或`集成_xxx`，例如`集成_riscv_top`、`集成_debug_top`。脚本会按工作簿顺序自动发现这些页签；为兼容历史工作簿，在没有这种名称时仍保留原有的内容结构识别。只有一个有效候选时直接生成，无论名称是`集成`还是`集成_xxx`；有多个候选时，终端 GUI 会要求选择，CLI 则使用`--integration`指定。一次运行只解析并生成所选集成层次引用的 TOP 和子模块，不会混入其他集成页签对应的模块。

内容结构上，同一行出现至少两组相邻的 `端口名`、`i/o` 表头时，该页签被识别为有效集成页签。每组的模块名位于 `端口名` 上方，既可以写 `RISCV_CORE`，也可以写 `module:RISCV_CORE`、`module：RISCV_CORE`；前缀不进入生成的模块名和实例名。若后续模块块已有模块名和端口数据但漏写重复的`端口名/i/o`表头，脚本也会恢复该块；缺失的集成方向按模块页定义校验并记录 info。

连接区由一个或多个空列隔开：

1. 第一个连续区是 `TOP ↔ 子模块`。第一组是 TOP，后续组是子模块；同一行的端口互连。
2. 后续包含至少两组模块的区是 `子模块 ↔ 子模块`。同一行生成一根内部 wire。固定数字或可解析宏的位宽不一致时，wire 取所有端点的最大位宽；较窄端口连接低位，唯一窄 output 未驱动的高位显式补零。
3. 后续只包含一组模块的区是“未连接端口”。子模块 input 接零，output/inout 使用空连接 `.port()`。
4. 集成表中完全遗漏的子模块端口也会按未连接端口处理，并产生 `info`；该自动诊断不会令 `--strict` 失败。
5. 未由子模块 output/inout 驱动的 TOP output 会在 TOP 内赋零；该 TOP output 可以同时连接一个或多个子模块 input，并以置零后的同一信号驱动它们。TOP input 始终只作为外部输入，不在模块内赋值。
6. TOP 端口引用末尾的 `[i]` 是 generate 指示符。脚本去掉指示符查找真实端口，在实例连接表达式中保留 `[i]`，并为对应子模块生成 `for (genvar i = 0; ...)`。能从首维默认值解析次数时输出 info；同一实例涉及多个范围时采用避免越界的最小值；完全无法解析时 warning 并使用 `< 1`。

列位置可以扩展，不限于示例中的 B～Y；关键是每个区内的模块组相邻、不同区之间至少留一个空列。脚本同时检查集成页签的 `i/o` 与模块定义是否一致、多驱动、缺失端口及位宽差异。

### V2 参数、宏和位宽裁决

- 同一模块内，同名宏/parameter 的非空数值必须一致；部分行留空时自动继承该模块的已知值。上层已有值而下层留空时也会向下传播。
- 所有 parameter 都可从 TOP 上游覆盖。TOP 已有同名 parameter 时，子模块实例统一传入 TOP 参数；只存在于子模块的 parameter 会提升为 TOP parameter，不再生成子模块局部 `localparam`。上下层 parameter 默认值不同时记录 `info`，实例显式传入 TOP 参数完成覆盖。
- parameter 参与的位宽不做“位宽不匹配”判断或固定切片，统一参数连接交给 SystemVerilog elaboration 解析。
- 宏只在集成 TOP 文件定义。上下层或兄弟模块的同名宏若给出不同默认值会报 error，不再猜测优先级；子模块桩文件不重复定义这些宏。
- 可确定的固定/宏位宽不一致仍输出 warning，但 V2 会生成可读的适配：接收端较窄时只接低位（单 bit 为 `[0]`），接收端较宽时高位补零；内部网络按最大位宽建线，窄驱动未覆盖的高位 assign 为零。数组、多维 packed 和 interface 只在能够安全确定语义时适配，否则保留形状诊断供人工处理。
- `Reporter` 现在有 `info`、`warning`、`error` 三级。`--strict` 只把 warning 视为失败；info 用于记录模板子集、参数上提、自动未连接等确定性的生成决策。

任意页签的表头行中若存在名为 `修改` 或 `修改列` 的列，读取 XLSX 时会先整列移除，再执行任何表头、模板、分类、方向和连接解析。该列可以插在业务列之间，其中所有内容均不会影响生成结果。

### 方向和模板报错排查

- `方向与模块定义不一致 (input/output)`：括号左侧是集成页签该模块列的 `i/o`，右侧是模块子页的定义；先按错误中的页签、行号和 `模块.端口` 核对这两个单元格。
- `TOP 输入 ... 与子模块输出 ... 方向冲突`：表示 TOP 的外部 input 被子模块 output 反向驱动。通常 TOP input 应连接子模块 input；若信号应由子模块驱动到芯片外部，TOP 方向应为 output。
- `模板端口展开不一致`（info）：诊断会列出各模块实际端口。V2 会按展开值取并集逐项处理；重点检查只存在于某一模块的展开项是否确实应按未连接语义生成。
- 排查时先运行 `python .\xlsx2verilog.py 文件.xlsx --check`，保留完整的第一条 error。前面的解析错误可能造成后续连接错误，不应只看最后一条诊断。

## 当前样例的说明

`test.xlsx` 可生成：

- `RISCV_TOP.v`：TOP 端口、APB 内部 wire、`RISCV_CORE_TEST` 和 `MEM_PHY` 实例；
- `RISCV_CORE_TEST.v`、`MEM_PHY.v`：端口定义及 output 赋零。

最新样例还包含 `test_bus_{{i}}_*` 和 `test_bus2_{{j}}_*` 的 `sig1/sig2/sig3` 展开、`sky_cs_if.mst` interface，以及位宽 `` `LANE_NUM * `Test_size`` 数组；后者生成 ``[`LANE_NUM -1:0][`TEST_SIZE -1:0] array``。模板宏按新规则生成 `DW_SIG1`～`DW_SIG3`；其 XLSX 默认值为不完整的 `155、…`，因此告警并使用 `114`。

新增 `test_bus2` 样例刻意保留了两个表格问题用于验证边界：`dat` 的端口名使用 `j`、位宽却引用未绑定的 `i`，因此三个端口均生成 `[114 -1:0]` 并告警；`valid` 写成 `{j}}`，脚本恢复为 `{{j}}` 后展开并要求修正 XLSX。这些 warning 是有意保留的待确认标记。

集成表中的三个 `test_bus_*_valid` 同时连接 `RISCV_CORE_TEST` 和 `MEM_PHY` 的 output，因此顶层 net 存在多驱动风险。脚本按表格生成并输出“多个子模块驱动端” warning；请在项目定义确定后修改 XLSX 方向或连接关系。

样例中两个子模块各有一个重复的 `ahb_test_5`，脚本按允许重复的规则合并到首次定义；APB 两端的数字位宽不同，脚本按最大位宽生成 wire、较窄端口使用低位切片、未驱动高位补零，并输出如下形式的警告：

```text
warning[RISCV_CORE_TEST.apb_3信号和MEM_PHY.apb_3信号应该连接，但是其位宽不匹配]
```

修正 Excel 后可使用 `--strict` 作为自动化流水线的质量门禁。

`review_test_cases/10_special_case/review2case.xlsx` 是本轮边界样例：其中 `APB_1`、`LANE_NUM` 的跨模块宏值冲突应报 error；在副本上分别扩散为统一值后可生成 4 个模块。TOP 的 5 bit `dft_test_en` 连接标量子模块输入时使用 `[0]`，`bus_in[i]` 和 `dyadic_bus_out_{{z}}[i]` 则驱动一个 5 次 `MEM_DAT` generate。原样例保留不修改，便于复验拒绝路径。

## 可视化附录工具

`appendix/` 提供两个完全离线的本地 Web GUI。它们只使用 Python 标准库、只绑定 `127.0.0.1`，并直接复用 `xlsx2verilog.py` 的解析与校验逻辑。

```powershell
# 可视化集成页签设计器
python .\appendix\run_designer.py

# XLSX / .xvlink.json 信号可视化阅读器
python .\appendix\run_viewer.py

# 无浏览器自动打开的统一启动方式
python -m appendix.server --mode designer --no-browser --port 8765
```

集成设计器支持选择 TOP/子模块 A/子模块 B、虚拟滚动端口列表、拖拽或键盘连线、可解释建议、高置信度批量接受、100 步撤销/重做、未连接确认、集成表预览及 `.xvlink.json` 工程保存。默认“另存为”；导出时先写临时 XLSX，再运行主生成器校验，成功后原子发布。原 module 页签的 OOXML 部件保持字节不变。

阅读器使用大框表示 TOP、两个内部小框表示子模块，以箭头显示驱动方向和扇出；左侧信号树可搜索/高亮网络，右侧显示端点、位宽、分类和原始解析诊断。完整操作、架构、打包和已知限制见 [`appendix/README.md`](appendix/README.md)。

## 测试

```powershell
python -m unittest discover -s tests -v
python -m py_compile .\xlsx2verilog.py
python .\tests\run_review_matrix.py
python .\tests\run_tech_review2_review.py
python .\xlsx2verilog.py .\review_test_cases\07_real_test_1\ibex_if_stage_3children.xlsx --check --strict
python .\xlsx2verilog.py .\review_test_cases\08_real_test_2\01_core_layer.xlsx --check --strict
python .\xlsx2verilog.py .\review_test_cases\08_real_test_2\02_if_stage_layer.xlsx --check --strict
# 以下两个原件刻意保留跨模块宏冲突，预期返回 2
python .\xlsx2verilog.py .\review_test_cases\09_version_2\test.xlsx --check
python .\xlsx2verilog.py .\review_test_cases\10_special_case\review2case.xlsx --check
# 扩散后的最小样例预期返回 0
python .\xlsx2verilog.py .\temp_test\variable_diffusion_after.xlsx --check
```

测试同样只使用标准库。除直接使用仓库中的 `test.xlsx` 外，`run_review_matrix.py` 还会创建 6 种不同结构的 XLSX，逐一调用本工具、立即静态检视生成结果，并在 `review_test_cases/检视报告.md` 中将发现归类。本轮新增规则由 `tests/test_version2_review1.py` 覆盖；`10_special_case` 原文件预期因宏冲突失败，不能把它当作 strict 成功样例。PRD 位于 `doc/PRD/`，代码维护文档及本轮带时间戳的 [`V2 Tech Review 1 实现与代码检视报告`](doc/TechReport/V2_TechReview1实现与代码检视报告_20260819_114122.md) 位于 `doc/TechReport/`。
