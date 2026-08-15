# XLSX → Verilog 生成器

`xlsx2verilog.py` 读取 Excel 中的模块定义和集成关系，为每个模块生成一个 `.v` 文件。脚本只依赖 Python 3.10+ 标准库，目标机器不需要联网，也不需要安装 `openpyxl`。

## 快速使用

```powershell
# 指定输入和输出目录
python .\xlsx2verilog.py .\test.xlsx -o .\generated

# 当前目录只有一个 xlsx 时可直接运行；有多个时会显示终端选择菜单
python .\xlsx2verilog.py

# 只查看识别结果
python .\xlsx2verilog.py .\test.xlsx --list

# 只校验，不写文件
python .\xlsx2verilog.py .\test.xlsx --check

# 将位宽不匹配等警告也视为失败
python .\xlsx2verilog.py .\test.xlsx --check --strict
```

正常生成的返回码为 `0`，校验失败为 `2`。文件以 UTF-8 和 LF 换行写入；已有同名 `.v` 会被原子替换，输出目录内其他文件不会被删除。

## XLSX 规则

脚本按表头识别内容，不要求模块端口从固定行开始。空行和合并的“分类”单元格不影响解析。

### 模块定义页签

每个模块占一个页签，必须包含以下表头：

| 表头 | 规则 |
|---|---|
| `端口名` | 合法的 Verilog 标识符；同一模块中的重复名称合并为同一个物理端口 |
| `位宽` | 正整数、宏（如 `` `DFT_BUS``）或 parameter（如 `UID_SIZE`） |
| `数值` | 宏或 parameter 的默认值；位宽为空时 `1` 表示标量 |
| `i/o` | 支持 `i/o/io`、`input/output/inout`、`输入/输出/双向` |

模块名取自 `端口名` 表头上方同一列最近的非空单元格；找不到时使用页签名。

- 宏位宽会生成带保护的 `` `define``，例如 `` `DFT_BUS = 64``。
- 非反引号符号位宽会生成模块 parameter。
- 数字位宽 `N` 会生成 `[N-1:0]`；数字 `1` 生成标量。
- 重复名称可以出现在多行或不同模块中。单个 Verilog 模块只声明一次同名端口；同一模块后续同名行合并到第一次定义。
- 普通模块页签中的所有 output 会在模块内部连续赋零，方便生成结果直接通过基础语法检查。

### 集成页签

同一行出现至少两组相邻的 `端口名`、`i/o` 表头时，该页签被识别为集成页签。每组的模块名位于 `端口名` 上方。

连接区由一个或多个空列隔开：

1. 第一个连续区是 `TOP ↔ 子模块`。第一组是 TOP，后续组是子模块；同一行的端口互连。
2. 后续包含至少两组模块的区是 `子模块 ↔ 子模块`。同一行生成一根内部 wire，wire 位宽取唯一 output 驱动端。
3. 后续只包含一组模块的区是“未连接端口”。子模块 input 接零，output/inout 使用空连接 `.port()`。
4. 集成表中完全遗漏的子模块端口也会按未连接端口处理，并产生警告。
5. 未由子模块驱动的 TOP output 会在 TOP 内赋零。TOP input 始终只作为外部输入，不在模块内赋值。

列位置可以扩展，不限于示例中的 B～Y；关键是每个区内的模块组相邻、不同区之间至少留一个空列。脚本同时检查集成页签的 `i/o` 与模块定义是否一致、多驱动、缺失端口及位宽差异。

## 当前样例的说明

`test.xlsx` 可生成：

- `RISCV_TOP.v`：TOP 端口、APB 内部 wire、`RISCV_CORE_TEST` 和 `MEM_PHY` 实例；
- `RISCV_CORE_TEST.v`、`MEM_PHY.v`：端口定义及 output 赋零。

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
```

测试同样只使用标准库。除直接使用仓库中的 `test.xlsx` 外，`run_review_matrix.py` 还会创建 6 种不同结构的 XLSX，逐一调用本工具、立即静态检视生成结果，并在 `review_test_cases/检视报告.md` 中将发现归类为“脚本问题”“生成的 XLSX 问题”或“项目定义的歧义”。
