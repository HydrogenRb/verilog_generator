# xlsx2verilog_merger

`xlsx2verilog_merger.py`是一个独立的、零第三方依赖的 Verilog 合并工具。它不读取 XLSX，也不导入主项目的`xlsx2verilog.py`：主生成器负责产生“新版本生成目录”，merger 负责把该目录安全更新到已有工程。

## 合并契约

合并以新生成文件为结构基准：模块端口、位宽、实例和 generate 等自动生成部分采用新版本；旧目标文件中每个`/*USER CODE BEGIN 标签*/`与对应`END`之间的内容按“标签 + 同标签出现序号”原样保留。USER 段外另保留三种显式人工决策：按区分大小写的`模块名.信号名`迁移`wire/reg`关键字；按`模块名.参数名`迁移`localparam/parameter`关键字；旧 assign 行带`//USER:`时，按模块和左值匹配并迁移整行。

例如旧项目把生成声明改为`reg [7:0] state;`，而新版本生成`wire [15:0] state;`，合并结果是`reg [15:0] state;`。旧项目的`parameter MODE = 1`遇到新生成的`localparam MODE = 2`，结果为`parameter MODE = 2`：只保留参数种类，新值仍进入项目。若模块名、信号名或参数名改变，则视为新对象，不继承旧关键字；新结构已经删除的旧对象也不会重新出现。

### 手工 assign 的最小保护协议

需要保留某条生成区 assign 时，在旧工程的完整单行 assign 末尾加入`//USER:`（允许写成`// USER:`，大小写不敏感）：

```verilog
assign status[7:4] = 4'hA; //USER: 项目要求的状态码
```

下次合并时，merger 优先按区分大小写的“模块名 + 去空白后的完整左值”查找新 assign，并用旧行整体替换它，因此左值切片、右值、缩进和注释都会保留。如果用户调整过 bit/part select 导致完整左值不同，则仅在同模块中该根信号只有一条新 assign 时回退到根信号匹配。例如旧`status[7:4]`可以唯一匹配新`status[15:8]`；若新结构有多条`status[...]`，merger 会拒绝猜测。

未标记的 assign 继续采用新生成版本。带`//USER:`的旧 assign 在新结构中不存在或匹配不唯一时，整批合并失败，防止手工逻辑静默丢失。当前协议有意只支持单行连续赋值；复杂、多行或条件逻辑应放进`/*USER CODE BEGIN ...*/`块。USER 块外的`//USER:`被保留为 assign 专用标记，格式不符合时会报错。

安全规则：

- 新旧 USER 标记必须完整、成对且不嵌套；损坏时整批拒绝写入。
- 如果新结构删除了一个含实际内容的旧 USER 段，整批拒绝写入，避免代码静默丢失。
- 先校验所有目标，再写任何文件；写入中途失败会恢复本轮已经替换的文件。
- 默认把会被覆盖的旧文件复制到带时间戳的备份目录；新文件不会伪造备份。
- 自定义备份目录必须位于新生成目录和目标项目目录之外。
- 新目录不存在的文件会创建；目标中多余的旧文件不会删除。
- `wire/reg`迁移只识别生成器采用的 ANSI 端口或单信号单行声明，并忽略 USER CODE 段内声明；同一`模块.信号`在自动区域同时声明成两种类型时拒绝猜测。
- `localparam/parameter`迁移只改变同名参数的关键字，数据类型、默认表达式、逗号和位置全部采用新版本。
- `//USER:` assign 必须是模块内完整的单行连续赋值；目标消失、重复或匹配歧义时整批拒绝写入。
- 只处理`.v`、`.sv`、`.vh`和`.svh`，目录层级按相对路径保持。
- 拒绝覆盖符号链接，避免写到项目目录之外。

## 使用方法

```powershell
# 先用主生成器产生新版本；输出目录与真实项目分开
py.exe .\xlsx2verilog.py .\design.xlsx --integration 集成_TOP -o .\new_generated

# 只检查合并计划，不写文件
py.exe .\appendix\xlsx2verilog_merger.py .\new_generated .\rtl_project --check

# 合并并自动备份被替换的旧文件
py.exe .\appendix\xlsx2verilog_merger.py .\new_generated .\rtl_project

# 自定义备份目录
py.exe .\appendix\xlsx2verilog_merger.py .\new_generated .\rtl_project --backup-dir .\backup\before_merge

# 明确不保留持久备份；事务失败时仍会用内存中的旧内容回滚
py.exe .\appendix\xlsx2verilog_merger.py .\new_generated .\rtl_project --no-backup
```

参数顺序固定为“新生成内容 → 已有项目”。源和目标都可以是单个 Verilog 文件；源为目录时，目标必须是目录。

返回码：成功为`0`，标记/路径/合并契约校验失败为`2`，其他文件系统错误为`3`。

## 与主生成器的边界

merger 不读取 XLSX 或集成关系，也不计算 Verilog 位宽。除 USER CODE 文本协议外，它只做保守的行级识别：识别普通`module/endmodule`范围、生成器风格的`wire/reg`和`localparam/parameter`声明，以及带`//USER:`的单行 assign。它不是完整 Verilog parser；复杂多信号同一行声明、宏生成声明、多行 assign 或语法重写仍应放入 USER CODE 段或交给专业 RTL 工具处理。

## 架构与结构变化

merger 分为“计划”和“执行”两阶段。计划阶段遍历新生成目录，以相对路径找到目标文件，依次解析 USER CODE 段、迁移`wire/reg`、迁移`localparam/parameter`、迁移显式`//USER:` assign，最后按“标签 + 同标签出现序号”填回旧 USER CODE 内容。每层替换后重新解析位置，避免关键字或整行长度变化造成偏移。任一文件校验失败时不会开始写入。执行阶段先建立备份并把全部结果写到同目录临时文件，再逐个原子替换；中途失败则按内存中的旧字节回滚。

新生成代码始终是结构真源，因此常见变化的结果如下：

| 场景 | 当前结果 | 如果要扩展保留范围 |
|---|---|---|
| 新代码增加例化 | 成功；新实例和新 USER 空段进入目标，旧同键 USER 内容继续保留；新增子模块文件会被创建 | 若会在一组同名 USER 标签之前插入实例，应把实例名写入标签，避免“同标签出现序号”整体后移 |
| 新代码减少例化 | 被删实例的 USER 段为空时成功；其中有内容时整批拒绝。新目录没有的旧子模块文件不会自动删除 | 清理旧文件应设计显式`--prune`清单、备份和确认；不要在默认合并中静默删除 |
| 旧代码把`localparam`改成`parameter` | 成功；保留旧`parameter`关键字，默认值和其余声明结构采用新版本 | 参数改名或删除时不迁移；同模块同名参数种类冲突时拒绝猜测 |
| 旧代码手改声明位宽、assign 右值，并写`//USER:` | 声明保留旧`wire/reg`种类但采用新位宽；带标记 assign 整行保留 | 无标记 assign 使用新版本；目标删除或多义时拒绝合并，复杂逻辑使用 USER CODE 块 |

目录计划只遍历新生成侧：目标项目中没有同路径新文件的旧文件保持原样。这使默认行为不会意外删除工程文件，但也意味着“减少子模块”后可能留下不再例化的旧 RTL 文件。

## 回归测试

```powershell
py.exe -m unittest discover -s appendix\tests -v
```

测试包含纯文本保护、`wire/reg`与`localparam/parameter`关键字迁移、显式`//USER:` assign 的精确/根信号/丢失目标处理、损坏标记拒绝、check-only、备份，以及第 14 号真实样例的“例化次数 10 改成 9”覆盖：新结构进入项目，旧 USER 段仍保留。
