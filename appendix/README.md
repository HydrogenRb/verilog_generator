# xlsx2verilog_merger V3.31

`xlsx2verilog_merger.py`是独立、零第三方依赖的 Verilog 合并工具，与主生成器共用`Version V3.31`。它不读取 XLSX，也不导入`xlsx2verilog.py`：主生成器负责产生“新版本生成目录”，merger 负责把该目录安全更新到已有生产工程。

## 合并契约

合并以新生成文件为结构真源：模块端口、位宽、实例和 generate 等自动生成内容采用新版本。旧生产文件中的人工决策通过以下四层协议迁移：

1. 每个`/*USER CODE BEGIN 标签*/`与对应`END`之间的内容，按“标签 + 同标签出现序号”原样保留。
2. 按区分大小写的`模块名.信号名`保留旧`wire/reg`关键字，但位宽、维度和其他声明结构采用新版本。
3. 按区分大小写的`模块名.参数名`保留旧`localparam/parameter`关键字，但值、数据类型和其他声明结构采用新版本。
4. USER 段外带`//USER:`或`//USER：`的受支持单行代码按稳定键整行保留。

例如旧项目把`wire [7:0] state;`改成`reg [7:0] state;`，新版本生成`wire [15:0] state;`，结果为`reg [15:0] state;`。旧项目的`parameter MODE = 1`遇到新生成的`localparam MODE = 2`，结果为`parameter MODE = 2`。若模块名、信号名或参数名改变，则视为新对象，不继承旧关键字。

### `//USER:`单行保护协议

标记不区分大小写，冒号支持半角`:`和全角`：`。merger 支持以下三类活动行或注释行：

```verilog
assign sig_a = 8'hA5;             //USER: 手工赋值
//assign sig_b                     //USER: 稍后赋值

input wire [7:0] sig_c;           //USER: 保留整行声明
//input wire sig_d                 //USER: 不使用

.sigA (temp_w_sig_a),             //USER: 改用临时信号
//.sigB (temp_w_sig_b)             //USER：不使用
```

匹配规则如下：

- assign 的键是“模块名 + 去空白的完整左值”。如果 bit/part select 已改变，仅当新模块内该根信号只有一条 assign 时才回退到根信号；注释 assign 可以只写左值。
- 声明的键是`模块名.信号名`。匹配后用旧行整体替换新声明，因此活动/注释状态、方向、类型、位宽、缩进及注释都保留。
- 实例端口连接的键是“当前父模块名 + 端口名”，不是实例名。如果同一父模块中同名端口连接出现多次，键不唯一，merger 会拒绝猜测。
- 目标不存在、同键重复、多个旧记录争用一个新目标，或标记所在行不是上述格式时，整批合并失败。
- 未标记的 assign、声明和端口连接采用新生成版本。复杂、多行或条件逻辑应放进 USER CODE 块。

被整行保留的注释代码必须自行维持合法的 Verilog 上下文。例如注释掉 ANSI 端口或实例端口后，用户需要确认前后逗号仍然合法。

## 安全与日志

- 新旧 USER 标记必须完整、成对且不嵌套。新结构删除了含实际内容的旧 USER 段时，整批拒绝写入。
- 先校验所有目标，再写任何文件；所有结果先写同目录临时文件，再逐个原子替换。中途失败会用内存中的旧字节回滚本轮已替换文件。
- 默认备份位于新代码侧，而不是生产工程侧：`<新生成路径名>.xlsx2verilog_merger_backup/<时间戳>/`。只备份将被覆盖的旧生产文件，新建文件不伪造备份。
- `--backup-dir`指定的目录必须同时位于新生成路径和目标生产路径之外；`--no-backup`只关闭持久备份，不关闭事务回滚。
- 新生成路径不得位于目标生产项目内部，目标项目也不得位于新生成目录内部。
- 目录计划只遍历新生成侧：新文件会创建，目标中没有对应新文件的旧文件不会删除。
- 只处理`.v`、`.sv`、`.vh`和`.svh`；拒绝覆盖符号链接。
- 每条保留或替换都会输出简短`info[...]`日志，包括关键字迁移、`//USER:`整行迁移、USER 段保留、目标文件覆盖/新建/不变、旧文件备份和结果写入。`--check`会打印计划与保留日志，但不会打印实际备份/写入日志。

## 使用方法

```powershell
# 先生成新代码；输出目录必须与生产工程分开
py.exe .\xlsx2verilog.py .\design.xlsx --integration 集成_TOP -o .\new_generated

# 查看 V3.31 版本
py.exe .\appendix\xlsx2verilog_merger.py --version

# 只检查合并计划，不写文件
py.exe .\appendix\xlsx2verilog_merger.py .\new_generated .\rtl_project --check

# 合并；默认备份建立在 new_generated 的同级新代码侧目录
py.exe .\appendix\xlsx2verilog_merger.py .\new_generated .\rtl_project

# 自定义备份目录
py.exe .\appendix\xlsx2verilog_merger.py .\new_generated .\rtl_project --backup-dir .\backup\before_merge

# 不保留持久备份；事务失败时仍回滚
py.exe .\appendix\xlsx2verilog_merger.py .\new_generated .\rtl_project --no-backup
```

参数顺序固定为“新生成内容 → 已有生产项目”。源和目标都可以是单个 Verilog 文件；源为目录时，目标必须是目录。成功返回`0`，标记/路径/合并契约校验失败返回`2`，文件系统错误返回`3`。

## 架构与结构变化

merger 不是完整 Verilog parser，而是面向生成器固定格式的保守文本合并器，分为“计划”和“执行”两阶段：

1. 计划阶段遍历新生成文件，以相对路径定位生产目标；解析 USER 段、模块、单行声明、参数、assign 和命名端口连接；逐层迁移旧决策，并在每层替换后重新解析位置。
2. 任一文件缺键、歧义或协议损坏时终止，生产目录保持不变。
3. 执行阶段在新代码侧建立备份，把全部结果暂存到各目标同目录，然后原子替换；异常时按旧字节逆序回滚。

常见结构变化：

| 场景 | 结果 |
|---|---|
| 新代码增加例化 | 新实例进入生产项目；已有同键 USER 内容和人工行继续保留 |
| 新代码减少例化 | 被删实例的 USER 段为空时成功；其中有内容或受保护行失去目标时拒绝合并 |
| 旧代码把`localparam`改成`parameter` | 保留旧关键字，采用新默认值和其余声明结构 |
| 旧代码手改 assign 并标记`//USER:` | 按左值整行保留；目标缺失或多义时拒绝合并 |
| 旧代码注释声明或`.port(...)`并标记`//USER:` | 按信号名或端口名整行保留；同父模块端口键重复时拒绝猜测 |

复杂的宏生成声明、多信号同行声明、多行 assign 或语法重写应放入 USER CODE 段，或交给专业 RTL 解析工具处理。

## 回归测试

```powershell
py.exe -m unittest discover -s appendix\tests -v
```

测试覆盖 USER 段、`wire/reg`、`localparam/parameter`、活动/注释 assign、活动/注释声明、活动/注释实例端口连接、缺失/歧义保护、逐项日志、check-only、新代码侧备份、事务回滚，以及第 14 号真实样例的结构覆盖。
