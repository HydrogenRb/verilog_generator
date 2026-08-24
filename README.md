# XLSX → Verilog 生成器

`xlsx2verilog.py` 读取 Excel 中的模块定义和集成关系，为每个模块生成一个 `.v` 文件。脚本只依赖 Python 3.10+ 标准库，目标机器不需要联网，也不需要安装 `openpyxl`。

当前能力的完整索引见[功能特性表](doc/TechReport/功能特性表.md)，内部结构见[代码结构与维护指南](doc/TechReport/代码结构与维护指南.md)。

已有 RTL 项目需要接收一版新的生成结果时，可使用独立附录工具[xlsx2verilog_merger](appendix/README.md)：它以新生成结构覆盖旧自动代码，同时按 USER CODE 标签保留人工代码，按稳定键保留用户选择的`wire/reg`和`localparam/parameter`关键字；旧工程中带`//USER:`的单行 assign 可整行保留。工具提供预检查、备份和失败回滚。

## 快速使用

```powershell
# 指定输入和输出目录
python .\xlsx2verilog.py .\main_test.xlsx --integration 集成_RISCV_TOP -o .\generated

# 无参数且在交互式终端中运行时，进入终端菜单
python .\xlsx2verilog.py

# 只查看识别结果
python .\xlsx2verilog.py .\main_test.xlsx --integration 集成_RISCV_TOP --list

# 一个工作簿有多个集成页签时，CLI 明确选择其中一个 TOP
python .\xlsx2verilog.py .\multi_top.xlsx --integration 集成_riscv_top -o .\generated

# 只校验，不写文件
python .\xlsx2verilog.py .\main_test.xlsx --integration 集成_RISCV_TOP --check

# 将位宽不匹配等警告也视为失败
python .\xlsx2verilog.py .\main_test.xlsx --integration 集成_RISCV_TOP --check --strict

# 扩散一个变量值（仍会要求输入 y/n；确认后原地修改 XLSX）
python .\xlsx2verilog.py .\design.xlsx --spread-value WIDTH "(3+5)"
```

命令行参数仍全部保留。无参数并且标准输入、输出均为交互式终端时，脚本会启动终端 GUI 菜单；使用 `↑`、`↓` 移动选项，按 `Enter` 确认，按 `Esc` 返回上级菜单或退出。主菜单包含“生成”“查看”“校验”“严格校验”“扩散变量值”和“退出”，可继续选择 XLSX 和输出目录。工作簿中有多个有效的集成页签时，菜单会再列出“页签名 → TOP 模块名”供选择；只有一个`集成`或`集成_xxx`页签时自动使用，不显示这一级菜单。显式传入 XLSX 或 `--list`、`--check`、`--strict` 等参数时直接按命令行模式执行，不显示菜单；多集成页签工作簿必须通过`--integration 页签名`消除歧义，管道、CI 等非交互环境不会等待输入。修改 XLSX 的 `--spread-value` 是唯一例外：无论从菜单还是 CLI 进入，都必须再次输入 `y` 才会修改。

每次直接运行脚本会先输出居中对齐的脚本名、`Version V3.2`、发布日期和联系方式。正常生成的返回码为 `0`，校验失败为 `2`。生成文件名统一为小写，例如模块`MEM_PHY`写入`mem_phy.v`，文件内的模块名仍保持大写。文件以 UTF-8 和 LF 换行写入；已有同名 `.v` 会在保留用户代码段后原子替换，输出目录内其他文件不会被删除。终端诊断按 ERROR、WARNING、INFO 分组，分别使用红、黄、青色；每条消息带稳定诊断代码，例如`warning[W_WIDTH_MISMATCH][...]`。重定向到文件或 CI 时自动关闭 ANSI 颜色。

## 文件顶部配置

所有脚本级开关都集中在`xlsx2verilog.py`开头的`User configuration`。可直接编辑生成文件头、是否覆盖旧文件头、条件块和三级诊断显示：

```python
VERILOG_FILE_HEADER = """//******************
// Design by: aaa
// Project name: bbb
//
//******************"""
OVERWRITE_FILE_HEADER = False
ENABLE_CONDITIONAL_BLOCKS = False
SHOW_ERROR_MESSAGES = True
SHOW_WARNING_MESSAGES = True
SHOW_INFO_MESSAGES = True

DIAGNOSTIC_VISIBILITY_BY_CODE = {
    "E_DIRECTION": True,
    "W_WIDTH_PLACEHOLDER": True,
    "W_WIDTH_MISMATCH": True,
    "W_ZERO_WIDTH": True,
    "W_NA_CONSTANT_WIDTH": True,
    "I_UNCONNECTED": True,
    # 其余代码见脚本开头的完整配置表
}
```

`OVERWRITE_FILE_HEADER=False`时，生成头位于`file header` USER 段，后续重新生成保留现有内容；设为`True`时用`VERILOG_FILE_HEADER`覆盖。`ENABLE_CONDITIONAL_BLOCKS=False`时，`条件：MACRO`只整理分类名称，不生成条件块。

三个`SHOW_*_MESSAGES`是错误、警告、信息的三级总开关。`DIAGNOSTIC_VISIBILITY_BY_CODE`是细粒度开关：把某个代码改为`False`，只隐藏该类型的终端输出。例如关闭`W_WIDTH_PLACEHOLDER`仍保留位宽不匹配`W_WIDTH_MISMATCH`；关闭`I_UNCONNECTED`不影响参数链接`I_PARAMETER_LINK`。总开关优先于细粒度开关。所有开关都只影响显示，诊断仍被记录，error 和`--strict`下的 warning 仍照常影响返回码与写入保护。

## XLSX 规则

脚本按表头识别内容，不要求模块端口从固定行开始。空行和合并的“分类”单元格不影响解析。

### 模块定义页签

每个模块占一个页签，必须包含 `端口名`、`位宽`、`数值` 和 `i/o` 表头；数组相关表头可选：

| 表头 | 规则 |
|---|---|
| `端口名` | 合法的 Verilog 标识符；同一模块中的重复名称合并为同一个物理端口 |
| `位宽` | packed 位宽；支持非负整数、整体带括号的安全整数表达式、宏（如 `` `DFT_BUS``）、parameter（如 `DATA_WIDTH`）、由任意顶层 `*` 分隔的多维 packed array，或 interface modport（如 `sky_cs_if.mst`） |
| `数值` | 位宽宏或 parameter 的匹配默认值；支持结果非负的整数表达式（包括 `0`）；普通端口位宽为空时仍按标量 `1` 处理 |
| `i/o` | 支持 `i/o/io`、`input/output/inout`、`输入/输出/双向`；`inout`声明不附加`wire`；interface 行使用 `NA`/`N/A`/`interface`；单元格为空时暂按 `inout` 生成，并在代码中加入 TODO 备注 |
| `数组`（可选） | 信号的外层 packed 数组维度；生成时统一放在端口名左侧、位宽之前；任意顶层 `*` 表示多维；别名为 `数组维度`、`数组深度`、`array`、`depth` |
| `数组数值`（可选） | 数组深度宏或 parameter 的默认值；别名为 `数组默认值`、`arrayvalue`、`array_default`、`depthdefault`、`depth_default` |

模块名取自 `端口名` 表头上方同一列最近的非空单元格；找不到时使用页签名。生成时模块名、宏和 parameter 规范为大写，默认实例名为`U_<MODULE>`；元数据表中的自定义例化名及普通信号、端口名严格保持 XLSX 中的大小写。输出文件名统一为小写。Verilog 标识符大小写敏感，因此`Data`和`data`是两个不同信号，集成页签也必须使用与模块页完全相同的拼写。

- 宏位宽只生成注释参考，例如 ``// `define DFT_BUS 64``，不会在真实项目中主动定义或重定义宏。
- 非反引号符号位宽会生成模块参数；当前版本默认使用不可由上层覆盖的`localparam`。
- 数字位宽 `N` 会生成 `[N -1:0]`；数字 `1` 生成标量；显式`0`仍会生成`[0 -1:0]`并输出`W_ZERO_WIDTH`，用于保留原始规格等待人工处理。
- 重复名称可以出现在多行或不同模块中。单个 Verilog 模块只声明一次同名端口；同一模块后续同名行合并到第一次定义。
- 普通模块页签中的所有 output 会在模块内部连续赋零，方便生成结果直接通过基础语法检查。

分类为`parameter`（也接受`parameters`、`参数`、`参数定义`）时，该分类内的行不是端口，而是当前模块的显式参数声明：`端口名`列填写参数名，`数值`列填写用于位宽匹配的非负整数默认值（允许 `0`），`i/o`留空。分类会向后续空分类行继承；名称统一转成大写，也支持`DW_{{i}}`逐项展开。`位宽`列留空时，生成值直接取`数值`；非空时则作为要生成的单行 Verilog 常量表达式，支持完整宏、宏调用、其他 parameter、系统函数、数值字面量及常用运算符。宏名和 parameter 引用随生成规范转成大写，系统函数名保持原样；`数值`列仍独立完成静态位宽匹配并生成行尾注释，不会尝试求值该表达式。旧式把完整宏直接写在`数值`列仍兼容。表达式不能包含换行、分号或注释，避免生成非预期语句。所有参数均不再生成`integer`。显式位宽、数组维度及宏/parameter 的匹配值允许为`0`，脚本会保留表达式并输出`W_ZERO_WIDTH`；空白位宽配`数值=0`仍按普通标量处理，避免把历史表格中的占位值误解释成零位宽。例化次数仍必须大于`0`。Verilog/SystemVerilog没有可移植的“零位宽 net”语义，因此`[0 -1:0]`只保证生成器不拒绝输入，不代表所有编译器都会接受，量产前必须处理该 warning。

例如以下三行（`数值`分别填写`4/5/3`）：

```text
parameter | para_a | `LANE_NUM     | 4
parameter | para_b | para_a+1      | 5
parameter | DW     | `log2(para_b) | 3
```

会按行序生成：

```verilog
localparam PARA_A = `LANE_NUM,      // 4
localparam PARA_B = PARA_A+1,       // 5
localparam DW     = `LOG2(PARA_B)   // 3
```

参数只有在集成页签中显式链接后才会改为可由上层覆盖的`parameter`；否则仍为`localparam`。

### 命名模板端口（`{{i}}`、`{{j}}`、`{{z}}` 等）

`端口名`、`位宽`和数组维度中可使用任意合法变量名的 `{{变量名}}`，不再限定为 `i`。同一行的任意备注单元格中用单层花括号给出对应取值，如 `j的范围是{sig1,sig2,sig3}` 或 `i是{a,b}`；也支持 `i为{...}`、`i={...}`、`i:{...}` 和 `i in {...}`。连续整数可简写为闭区间，例如`z=0:31`等价于列出`0`到`31`共 32 项，`z=31:0`则按降序展开；单个范围最多 4096 项。变量列表会向同一“分类”的后续空分类行继承。例如：

```text
端口名: test_bus_{{i}}_dat
位宽:   `DW_{{i}}
备注:    i的列表是{sig1,sig2,sig3}
```

会展开为 `test_bus_sig1_dat`、`test_bus_sig2_dat`和 `test_bus_sig3_dat`，对应宏为 `` `DW_SIG1``～`` `DW_SIG3``。模板生成的信号名和取值大小写均原样保留，展开后的反引号宏标识符仍统一转换为大写；模板取值同时用于跨模块逐项、区分大小写地对齐。若模板宏/parameter 的`数值`无法可靠计算，脚本输出 warning 并使用 `114` 作为明确的待确认占位值。集成页签中保留原始 `{{i}}` 写法即可，脚本会按模块已展开端口逐项连接。

变量按名称绑定：`{{j}}` 只使用 `j的范围是{...}`，不会自动套用 `i` 的列表。同一端口名包含多个变量时采用笛卡尔积，例如 `bus_{{j}}_{{z}}` 配合 `j={a,b}`、`z={x,y}` 会生成 `bus_a_x`、`bus_a_y`、`bus_b_x`、`bus_b_y`。如果位宽或数组维度引用了未被端口名绑定的其他变量，脚本不会猜测变量之间的关系，而是 warning 后使用 `114`；如果端口名本身的变量没有取值列表，则报错且不生成该行。

集成页签中的模板引用按“来源模板 + 展开取值”匹配，不按最终端口名做前缀通配。例如 `data_{{i}}` 只匹配由该模板生成的端口，不会误匹配普通端口 `data_debug`。不同模块的取值顺序可以不同，脚本按值连接。V2 允许各模块展开集合不同：共有取值正常互连，仅存在于 TOP 或某个子模块的展开项按单端/未连接语义处理，并输出 `info` 说明，不再因数量或取值集合不同而中止生成。

对于可明确恢复的单花括号拼写错误（例如 `{j}}` 或 `{{j}`），脚本会按 `{{j}}` 处理并告警，XLSX 仍应修正。`i/o` 空白是另一类待确认数据：声明会生成为 `inout`，并附加 `/* TODO: XLSX i/o 为空...需处理方向缺失问题 */` 备注，同时输出 warning；无法识别的非空方向仍然报错。

### 变量值扩散（会修改 XLSX）

终端菜单的“扩散变量值”或 CLI 的 `--spread-value VARIABLE VALUE` 一次只处理一个宏/parameter。脚本先执行完整解析并展示现有 error/warning，然后要求输入 `y` 或 `n`；只有 `y` 会继续。确认后，原文件先复制到同级 `backup/`，文件名带微秒级时间戳，再通过临时 ZIP 原子替换当前 XLSX。输入 `n` 不修改文件，也不创建备份。

`parameter`分类中的显式声明也参与变量发现和扩散；扩散一个 parameter 时，会同时更新其显式声明行及使用该 parameter 的位宽行，避免声明值和引用值产生新的局部冲突。

扩散值允许非负整数或整体带括号、可安全计算且结果非负的整数表达式，例如 `0`、`8`、`(3-3)`、`(3+5)`；不接受未加外层括号的 `3+5`，也不接受负数结果。普通单维直接覆盖`数值`；多维只覆盖变量所在因子，其他空因子使用可计算的数字位宽，否则填 `114`。模板变量会写成显式范围，例如把 `BUS_REQ` 扩散为 `(3+5)` 后可得到：

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
| `0` | 空白 | `input wire [0 -1:0] sig` + `W_ZERO_WIDTH` | 原样保留零位宽规格；需人工确认下游工具支持方式 |
| `8` | 空白 | `input wire [8 -1:0] sig` | 固定 8 bit 向量；对齐补白位于 `8` 与 `-1:0]` 之间 |
| `(2+3*4)` | 空白 | `input wire [14 -1:0] sig` | 括号内表达式先计算为 14 |
| `` `DATA_W`` | `32` | ``// `define DATA_W 32``，端口为 ``[`DATA_W -1:0]`` | 单个宏；只输出注释参考，不主动定义 |
| `DATA_WIDTH` | `32` | `localparam DATA_WIDTH = 32`，端口为 `[DATA_WIDTH -1:0]` | 默认本模块局部参数 |
| `` `LANE_NUM*DATA_WIDTH`` | `4*32` | ``[`LANE_NUM -1:0][DATA_WIDTH -1:0] sig`` | 无论乘号是否带空格，都按两个 packed 维度解释 |
| `TOTAL_WIDTH` | `(4*32)` | `[TOTAL_WIDTH -1:0] sig` | 括号使默认值保持单维，计算为 128 |
| `DATA_WIDTH`，`数组=DEPTH` | `32`，`数组数值=4` | `input wire [DEPTH -1:0][DATA_WIDTH -1:0] sig` | 数组维度在最左侧，元素位宽紧随其后 |
| `sky_bus_if.mst` | 空白 | `sky_bus_if.mst sig` | interface modport，不生成 `wire` |

复杂项目示例：若需要 4 lane、每 lane 32 bit 的二维 packed 端口，应写 `` `LANE_NUM*DATA_WIDTH`` 和数值 `4*32`。若需要单根 128 bit 扁平总线，应使用单独的 `TOTAL_WIDTH`，数值写 `128` 或 `(4*32)`。不确定的模板位宽会 warning 并使用 `[114 -1:0]` 作为待确认占位值。

`位宽`描述每个元素的 packed width，`数组`描述位于其外侧的 packed dimensions。所有普通信号维度统一位于名称左侧，顺序是“数组维度 → 位宽中的多维因子 → 最终元素位宽”。例如 `DATA_WIDTH` 的数值为 `32`、`DEPTH` 的数组数值为 `4` 时生成：

```systemverilog
input wire [DEPTH -1:0][DATA_WIDTH -1:0] data
```

未连接的多维 input 使用`'0`接零；普通模块及未驱动 output 也直接使用整信号`assign signal = '0`，不再为置零生成逐元素循环。多维 packed 端口和 interface 属于 SystemVerilog 语法：生成文件仍使用 `.v` 扩展名，仿真、综合和 lint 工具必须显式启用 SystemVerilog 模式。interface 实例数组受 SystemVerilog 语法限制，若使用则仍只能写在 interface 实例名右侧；普通 input/output/inout/wire 不存在这一例外。

### Interface

当`位宽`是 `interface_type.modport`（如 `sky_cs_if.mst`）且 `i/o` 为 `NA`时，生成 `sky_cs_if.mst chi_if_risc`形式的 SystemVerilog interface 端口。interface 端口名与同一模块的普通 input/output/inout 端口名使用相同起始列。集成页签同样使用 `NA`，实例按名连接。生成器不生成 interface 本身的定义，编译时需由项目提供。

## 生成代码格式

生成器会按当前代码块中最长的字段统一排版：普通端口与 interface 的端口名按列对齐；普通信号的全部 packed 范围都在名称左侧并按维度建立独立列，每一维的左方括号固定，数字、参数名或宏名紧跟 `[` 并左对齐，所需空格放在表达式与减号之间，使该维的 `-1:0]` 仍然纵向对齐，第二维及后续维度使用相同规则；所有生成的 `-1:0` 前至少保留一个空格。同一组宏参考、参数名、wire 名和 assign 等号分别对齐；模块实例声明从第 1 列开始，参数连接和端口连接只缩进 4 个空格，左、右圆括号均纵向对齐。每个 generate 实例使用独立的`genvar i_gen_<实例名>`，避免同一作用域重复声明`i`。模块级 `assign` 和对应说明注释从第 1 列开始。模块主体结束后直接生成 `endmodule`，不在其前方保留空白行。

```verilog
input wire [`SHORT     -1:0][`LANE    -1:0] matrix_a,
input wire [`LONG_NAME -1:0][`CHANNEL -1:0] matrix_b

CHILD U_CHILD (
    .short_port (a_signal        ),
    .long_port  (long_signal_name)
);
```

### 可持久化用户代码段

每个文件开头都有`file header`代码段，每个模块端口声明后都有`before statement`代码段；集成 TOP 在每个子模块实例块前后还分别生成`before <MODULE>`和`after <MODULE>`代码段：

```verilog
/*USER CODE BEGIN before statement*/
// 可以在这里填写声明或逻辑
/*USER CODE END   before statement*/

/*USER CODE BEGIN before CHILD*/
// 可以在这里填写实例前逻辑
/*USER CODE END   before CHILD*/
CHILD U_CHILD (...);
/*USER CODE BEGIN after CHILD*/
// 可以在这里填写实例后逻辑
/*USER CODE END   after CHILD*/
```

重新生成时，脚本按“标签 + 同名标签出现序号”提取旧文件中 BEGIN/END 之间的原文，再合入新生成结构。不要编辑、删除或嵌套标记行；若标记损坏、BEGIN/END 标签不一致，或包含实际用户内容的旧代码段在新结构中失去对应位置，本次生成会报 error 并在写文件前整体停止，以免静默丢失用户代码。`--check`也会执行相同的保护校验。

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

内容结构上，同一行出现至少两组相邻的 `端口名`、`i/o` 表头时，该页签被识别为有效集成页签。每组的模块名位于 `端口名` 上方，既可以写 `RISCV_CORE`，也可以写 `module:RISCV_CORE`、`module：RISCV_CORE`；前缀不进入生成的模块名和实例名。若后续模块块已有模块名和端口数据但漏写重复的`端口名/i/o`表头，脚本也会恢复该块；缺失的集成方向按模块页定义校验并记录 info。唯一例外是作为连接终点的匿名 `NA` 列：它可以紧跟某个正常的`端口名/i/o`列对，不写模块名，也不写自己的表头。

连接区由一个或多个空列隔开：

1. 第一个连续区是 `TOP ↔ 子模块`。第一组是 TOP，后续组是子模块；同一行的端口互连。
2. 所有后续连接区统一按真实端点数量处理，不再区分“单模块未连接区”和“子模块互连区”。同一行有两个或更多真实模块端点时生成一根内部 wire；wire 名使用唯一 output 驱动端的端口名，例如`producer_out → consumer_in`生成`w_producer_out`，不再生成`w_producer_out_to_consumer_in`。固定数字或可解析宏的位宽不一致时，wire 取所有端点的最大位宽；较窄端口连接低位，唯一窄 output 未驱动的高位显式补零。
3. 后续连接区一行只有一个真实模块端点且没有 NA 对端时，按未连接处理：子模块 input 接零，output/inout 使用空连接`.port()`；若旁边存在`NA/NA->name/NA->常量`端点，则改用与多模块区完全相同的 NA 占位处理。
4. 集成表中完全遗漏的子模块端口也会按未连接端口处理，并产生 `info`；该自动诊断不会令 `--strict` 失败。
5. 未由子模块 output/inout 驱动的 TOP output 会在 TOP 内赋零；该 TOP output 可以同时连接一个或多个子模块 input，并以置零后的同一信号驱动它们。TOP input 始终只作为外部输入，不在模块内赋值。
6. TOP 端口引用末尾的 `[i]` 是 generate 指示符。脚本去掉指示符查找真实端口，并把连接表达式中的索引改为该实例独有的`i_gen_<实例名>`。能从首维默认值解析次数时输出 info；同一实例涉及多个范围时采用避免越界的最小值；完全无法解析时 warning 并使用 `< 1`。
7. TOP 端口可以显式写常量 bit select，例如 5 bit 的`n_rst`写成`n_rst[0]`连接子模块端口。脚本校验索引未越界，并原样保留`.n_rst (n_rst[0])`；显式选择优先于自动位宽适配。
8. 子模块互连区的一端可以写`NA`或`N/A`。无论另一端是 input、output 还是 inout，TOP 内的普通占位信号都统一创建为同形状`wire`。实例仍以占位信号连接并附加 TODO。`NA[i]`会生成实例循环和带额外最左维度的占位 wire。`NA`端允许省略模块名和重复表头。在第一个`TOP ↔ 子模块`连接区，TOP 端口的对端写`NA`表示保留顶层观察端口；模板端口仍正常展开。
9. `NA->名称`为占位线指定精确名称，例如`NA->ready_test_process`生成同形状`wire ... ready_test_process`并连接真实模块端口；这项自动形状能力为原有功能，无需增加新的表格列。`NA->0`和`NA->1`按目标端口全部 packed 维度生成全 0/全 1 复制式，例如`[A][B]`信号生成`{A*B{1'b1}}`。`NA->8'hFF`等定宽常量会与可解析的目标总位宽比较：较窄时在高位显式补零，等宽时原样使用，较宽时输出`W_NA_CONSTANT_WIDTH`并保留原常量，由 Verilog 连接上下文截断。对于 TOP output，生成对应`assign`且同一行的子模块 output 开路，避免把常量接到输出端。未连接、单端和 NA 占位类诊断均为 info，不会使`--strict`失败。
10. 集成页签可在任意空白区域增加`模块名 / 例化名 / 例化次数`三列表。未填写时沿用`U_<模块名>`和由`[i]`推导的次数；显式例化名保持用户大小写，显式次数直接控制 generate 上限。若次数超过可解析索引范围，输出越界风险 warning。
11. 任一连接区的分类列可写`parameter`并向下继承。该行引用的是各模块参数而非端口；只有显式链接的子模块参数生成可覆盖`parameter`并出现在`#(...)`连接中。若链接仅发生在两个子模块之间，脚本会在 TOP 自动创建同名或唯一化的`localparam`并输出 info。

列位置可以扩展，不限于示例中的 B～Y；关键是每个区内的模块组相邻、不同区之间至少留一个空列。脚本同时检查集成页签的 `i/o` 与模块定义是否一致、多驱动、缺失端口及位宽差异。

NA 必须写在真实端口的“对端”单元格中，不能用它替代需要查找的真实端口名。各连接区的精确范围如下：

| 位置 | `NA` | `NA->signame` | 说明 |
|---|---|---|---|
| 第一块`TOP ↔ 子模块` | 支持 | 支持 | TOP 列写真实端口、对端写 NA 时可保留/命名观察端口；TOP 列直接写`NA->signame`、子模块列写真实端口时，则为该子模块端口创建命名占位 wire；`NA->常量`可给 TOP output 或子模块 input 赋值 |
| 任意后续连接区 | 支持 | 支持 | 只需一行保留至少一个真实子模块端口；`NA`创建自动命名占位 wire，`NA->signame`创建指定名称 wire，二者都在实例连接处加入 TODO |
| 后续区没有 NA 对端 | 不适用 | 不适用 | 一个真实 input 自动接零，一个真实 output/inout 自动开路；两个以上真实端点按内部互连处理 |

TOP 连接区的`NA->0/NA->1`属于常量驱动功能；`NA->signame`属于命名观察功能。后者不会改写 TOP 端口名，也不会增加 TOP 驱动，只创建一根观察 wire，因此可以与同一行已有的正常子模块连接并存。interface TOP 端口不支持连续赋值观察 wire，会输出`E_INTERFACE_CONNECTION`。

### V3.2 参数、宏和位宽裁决

- 同一模块内，同名宏/parameter 的非空数值必须一致；部分行留空时自动继承该模块的已知值。上层已有值而下层留空时也会向下传播。
- 所有参数默认局部：生成`localparam`且实例不传参。只有集成页签`parameter`分类中显式链接的子模块参数才生成`parameter`，并由 TOP localparam 传入。没有 TOP 端点的子模块互连会自动创建 TOP localparam，并输出 info。
- parameter 参与的位宽不做“位宽不匹配”判断或固定切片，统一参数连接交给 SystemVerilog elaboration 解析。
- 宏参考只在集成 TOP 文件集中列为注释。上下层或兄弟模块的同名宏若给出不同匹配值会报 error，不再猜测优先级；错误会逐项列出页签与数值。子模块桩文件不重复列出这些宏。
- 可确定的固定/宏位宽不一致仍输出 warning，但 V2 会生成可读的适配：接收端较窄时只接低位（单 bit 为 `[0]`），接收端较宽时高位补零；内部网络按最大位宽建线，窄驱动未覆盖的高位 assign 为零。数组、多维 packed 和 interface 只在能够安全确定语义时适配，否则保留形状诊断供人工处理。
- `NA->定宽常量`使用目标所有 packed 维度的乘积做比较；源常量较窄时高位补零，源常量较宽时 warning 后保留原式。目标维度无法静态求值时不猜测宽度，保留常量并输出 warning。
- 显式零位宽只走“保留并诊断”路径，不参与最大宽度 wire、切片或自动适配计算，避免生成负索引之外的二次错误。完整裁决矩阵见[《V3.2 位宽不匹配分析》](doc/TechReport/V3.2位宽不匹配分析.md)。
- `Reporter` 有 `error`、`warning`、`info` 三级，输出时按严重程度分组并在交互终端使用红、黄、青色。`--strict`只额外把 warning 视为失败；三个显示开关可独立隐藏输出，但不改变判定。

任意页签的表头行中若存在名为 `修改` 或 `修改列` 的列，读取 XLSX 时会先整列移除，再执行任何表头、模板、分类、方向和连接解析。该列可以插在业务列之间，其中所有内容均不会影响生成结果。

### 方向和模板报错排查

- `方向与模块定义不一致 (input/output)`：括号左侧是集成页签该模块列的 `i/o`，右侧是模块子页的定义；先按错误中的页签、行号和 `模块.端口` 核对这两个单元格。
- `TOP 输入 ... 与子模块输出 ... 方向冲突`：表示 TOP 的外部 input 被子模块 output 反向驱动。通常 TOP input 应连接子模块 input；若信号应由子模块驱动到芯片外部，TOP 方向应为 output。
- `模板端口展开不一致`（info）：诊断会列出各模块实际端口。V2 会按展开值取并集逐项处理；重点检查只存在于某一模块的展开项是否确实应按未连接语义生成。
- 排查时先运行 `python .\xlsx2verilog.py 文件.xlsx --check`，保留完整的第一条 error。前面的解析错误可能造成后续连接错误，不应只看最后一条诊断。

## 当前样例的说明

`main_test.xlsx`包含`集成_RISCV_TOP`和`集成_RISCV_CORE_TEST`两个集成候选，CLI 示例需显式传入`--integration`，终端 GUI 则可直接选择。选择`集成_RISCV_TOP`可生成：

- `riscv_top.v`：TOP 端口、APB 内部 wire、`RISCV_CORE_TEST` 和 `MEM_PHY` 实例；
- `riscv_core_test.v`、`mem_phy.v`：端口定义及 output 赋零。

最新样例还包含 `test_bus_{{i}}_*` 和 `test_bus2_{{j}}_*` 的 `sig1/sig2/sig3` 展开、`sky_cs_if.mst` interface，以及位宽 `` `LANE_NUM * `Test_size`` 数组；后者生成 ``[`LANE_NUM -1:0][`TEST_SIZE -1:0] array``。模板宏按新规则生成 `DW_SIG1`～`DW_SIG3`；其 XLSX 默认值为不完整的 `155、…`，因此告警并使用 `114`。

新增 `test_bus2` 样例刻意保留了两个表格问题用于验证边界：`dat` 的端口名使用 `j`、位宽却引用未绑定的 `i`，因此三个端口均生成 `[114 -1:0]` 并告警；`valid` 写成 `{j}}`，脚本恢复为 `{{j}}` 后展开并要求修正 XLSX。这些 warning 是有意保留的待确认标记。

集成表中的三个 `test_bus_*_valid` 同时连接 `RISCV_CORE_TEST` 和 `MEM_PHY` 的 output，因此顶层 net 存在多驱动风险。脚本按表格生成并输出“多个子模块驱动端” warning；请在项目定义确定后修改 XLSX 方向或连接关系。

样例中两个子模块各有一个重复的 `ahb_test_5`，脚本按允许重复的规则合并到首次定义；APB 两端的数字位宽不同，脚本按最大位宽生成 wire、较窄端口使用低位切片、未驱动高位补零，并输出如下形式的警告：

```text
warning[W_WIDTH_MISMATCH][RISCV_CORE_TEST.apb_3信号和MEM_PHY.apb_3信号应该连接，但是其位宽不匹配]
```

修正 Excel 后可使用 `--strict` 作为自动化流水线的质量门禁。

`review_test_cases/10_special_case/review2case.xlsx` 是本轮边界样例：其中 `APB_1`、`LANE_NUM` 的跨模块宏值冲突应报 error；在副本上分别扩散为统一值后可生成 4 个模块。TOP 的 5 bit `dft_test_en` 连接标量子模块输入时使用 `[0]`，`bus_in[i]` 和 `dyadic_bus_out_{{z}}[i]` 则驱动一个 5 次 `MEM_DAT` generate。原样例保留不修改，便于复验拒绝路径。

`review_test_cases/12_test_for_techreview2/techreview2version2.xlsx`覆盖本轮新增规格：显式 parameter 分类、子模块端口连接 NA、常量 bit select、generate 排版和带页签来源的宏冲突诊断。原件刻意保留四组宏冲突，预期直接校验失败；自动测试在临时副本上统一冲突值后检查 4 个小写 Verilog 文件，不修改原始 XLSX。

`review_test_cases/13_test_for_techreview3/techreview2version3.xlsx`覆盖用户代码段、`NA[i]`内部 generate、`z=0:31`范围展开和`RISCV_CRG`的 32 个模板端口。原件继承四组刻意保留的宏冲突，预期直接校验失败；`tests_script/test_version2_review3.py`在临时副本上统一宏值后回归。

`review_test_cases/14_edge_case_test_problem/eage_case.xlsx`是 V3 主验收样例：参数分类在 TOP 和`RISCV_CORE_TEST`间显式链接；`RST_LANE`使用`` `GLB_RST_LANE``并以`1`匹配；`NA->ready_test_process`创建命名 wire；`NA->1`把三个 TOP valid 输出赋 1；`MEM_DAT`使用`PROJECT_PERSONAL_MEM_DAT`实例名；`RISCV_CRG`显式例化 10 次。该样例仍刻意保留模板变量、占位参数和位宽差异 warning，因此普通`--check`成功，`--strict`预期失败。

`review_test_cases/17_v3_techreview2_width_boundary/width_boundary.xlsx`是 V3.2 位宽边界样例，集中覆盖固定宽度扩展/截断、内部网络取最大宽度、参数位宽延后到 elaboration、多维形状差异、显式零位宽、`NA->0/1`复制以及`NA->8'hFF`补零/过宽 warning。预期普通`--check`返回`0`，并产生`1`条`W_ZERO_WIDTH`、`8`条`W_WIDTH_MISMATCH`和`1`条`W_NA_CONSTANT_WIDTH`；详细逐行结论见该目录下的[检视报告](review_test_cases/17_v3_techreview2_width_boundary/检视报告.md)。

## 文档归档

需求按版本归档在`doc/PRD/`；当前维护资料与功能索引位于`doc/TechReport/`；历史检视报告位于`doc/TechReport/design_review/`；已移除附录工具的历史需求仅保存在`doc/appendix需求/`，不代表当前仓库仍包含对应可执行代码。

## 测试

```powershell
python -m unittest discover -s tests_script -v
python -m unittest discover -s appendix\tests -v
python -m py_compile .\xlsx2verilog.py
python .\tests_script\run_review_matrix.py
python .\tests_script\run_tech_review2_review.py
python .\xlsx2verilog.py .\review_test_cases\07_real_test_1\ibex_if_stage_3children.xlsx --check --strict
python .\xlsx2verilog.py .\review_test_cases\08_real_test_2\01_core_layer.xlsx --check --strict
python .\xlsx2verilog.py .\review_test_cases\08_real_test_2\02_if_stage_layer.xlsx --check --strict
# 以下三个原件刻意保留跨模块宏冲突，预期返回 2
python .\xlsx2verilog.py .\review_test_cases\09_version_2\test.xlsx --check
python .\xlsx2verilog.py .\review_test_cases\10_special_case\review2case.xlsx --check
python .\xlsx2verilog.py .\review_test_cases\13_test_for_techreview3\techreview2version3.xlsx --check
# V3 主验收样例：普通检查返回 0；其已知 warning 会令 --strict 失败
python .\xlsx2verilog.py .\review_test_cases\14_edge_case_test_problem\eage_case.xlsx --check
# V3.2 位宽边界样例：普通检查返回 0；其已知 warning 会令 --strict 失败
python .\xlsx2verilog.py .\review_test_cases\17_v3_techreview2_width_boundary\width_boundary.xlsx --check
```

测试同样只使用标准库。`run_review_matrix.py`会创建 6 种不同结构的 XLSX 并静态检视生成结果。V2 三轮回归位于`tests_script/test_version2_review*.py`；V3.1 与 V3.2 规则分别由`tests_script/test_version3_review1.py`、`tests_script/test_version3_review2.py`覆盖；独立 merger 的事务与第 14 号真实覆盖回归位于`appendix/tests/`。`10_special_case`、`12_test_for_techreview2`与`13_test_for_techreview3`原文件预期因宏冲突失败，不能当作 strict 成功样例。参数与集成功能的历史实现结论见[`V3.1 TechReview1 实现与代码检视报告`](doc/TechReport/design_review/V3_TechReview1实现与代码检视报告_20260824.md)，本轮裁决矩阵见[《V3.2 位宽不匹配分析》](doc/TechReport/V3.2位宽不匹配分析.md)。
