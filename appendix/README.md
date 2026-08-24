# xlsx2verilog_merger

`xlsx2verilog_merger.py`是一个独立的、零第三方依赖的 Verilog 合并工具。它不读取 XLSX，也不导入主项目的`xlsx2verilog.py`：主生成器负责产生“新版本生成目录”，merger 负责把该目录安全更新到已有工程。

## 合并契约

合并以新生成文件为结构基准：模块端口、wire、assign、实例和 generate 等自动生成部分采用新版本；旧目标文件中每个`/*USER CODE BEGIN 标签*/`与对应`END`之间的内容按“标签 + 同标签出现序号”原样保留。USER 段之外的手工修改会被新生成结构覆盖。

安全规则：

- 新旧 USER 标记必须完整、成对且不嵌套；损坏时整批拒绝写入。
- 如果新结构删除了一个含实际内容的旧 USER 段，整批拒绝写入，避免代码静默丢失。
- 先校验所有目标，再写任何文件；写入中途失败会恢复本轮已经替换的文件。
- 默认把会被覆盖的旧文件复制到带时间戳的备份目录；新文件不会伪造备份。
- 自定义备份目录必须位于新生成目录和目标项目目录之外。
- 新目录不存在的文件会创建；目标中多余的旧文件不会删除。
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

merger 只理解 USER CODE 文本协议，不理解模块、端口、位宽、XLSX 或集成关系。因此主生成器可以独立使用，merger 也可以合并任何遵守相同 USER 标记协议的 Verilog 生成器输出。这一边界避免复制 XLSX 解析和 RTL 建模逻辑。

## 回归测试

```powershell
py.exe -m unittest discover -s appendix\tests -v
```

测试包含纯文本保护、损坏标记拒绝、check-only、备份，以及第 14 号真实样例的“例化次数 10 改成 9”覆盖：新结构进入项目，旧 USER 段仍保留。
