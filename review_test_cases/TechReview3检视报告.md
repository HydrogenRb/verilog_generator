# Tech Review 3 整体代码检视报告

## 结论

Tech Review 3 与紧急追加需求均已实现。整体审查未发现尚未处理的阻断性脚本问题；单元测试、历史矩阵、三个真实项目样例和生成代码静态审计全部通过。

## 需求追踪

| 需求 | 实现与检视结果 |
|---|---|
| interface 名称对齐 | 与普通 input/output/inout 端口共用名称起始列，回归通过 |
| 分类代码块 | 连续分类生成分隔注释，无分类使用 `no group`，回归通过 |
| 条件分类 | 支持 `条件：FEATURE` 与 ``条件：`FEATURE``；声明、output 赋零、实例连接同步受控，四种宏组合回归通过 |
| 条件宏不自动定义 | 文件头不生成条件宏，回归通过 |
| 宏直接定义 | 使用对齐的 `` `define``，不生成保护性 `` `ifndef``，回归通过 |
| 宏、wire、assign、localparam 对齐 | 各自代码块内按最长字段对齐，回归通过 |
| 集成模块名前缀 | 直接模块名、`module:`、`module：` 均可解析，回归通过 |
| README specification | 已补充位宽、乘号空格、多维 packed/unpacked、interface 与复杂例子 |
| 中英文局部参数注释 | 英文注释保留，并增加中文注释 |
| 可视化连线软件需求文档 | 已交付 `可视化集成页签生成器需求文档.md` |

## 整体代码审查

- 解析层：条件宏仅接受合法 Verilog 标识符；分类注释移除换行，避免注释注入；`修改/修改列`仍在任何业务解析前整列忽略。
- 展开层：模板按来源与变量取值对应，不使用宽泛名称匹配，避免普通端口被误认为模板展开结果。
- 连接层：TOP output 到子模块 input 保持合法；TOP input 到子模块 output 仍为错误；条件化驱动端与 TOP output 条件不一致时阻止生成。
- 生成层：条件端口的声明、默认赋值与实例连接保持同步。实例连接使用生成器内部、用后清理的临时预处理标记，保证任意条件组合都只有正确数量的逗号。
- 文件安全：有解析错误或 strict warning 时不写输出；正常写入继续使用同目录临时文件后替换，避免直接截断目标文件。
- 依赖与兼容：运行和测试只依赖 Python 标准库；Windows 中文路径及真实样例均已回归。

审查期间发现并修复一项连带风险：最初条件只包裹端口声明，关闭条件后桩模块赋值或父模块实例仍可能引用已移除端口。现已把条件传播到赋值和绑定，并新增集成级预处理组合测试。

## 验证记录

- `python -B -m unittest discover -s tests -v`：22/22 通过。
- `python -B tests/run_tech_review2_review.py`：三轮全部通过。
- Tech Review 1 自动生成矩阵：6/6 通过。
- `review_test_cases/07_real_test_1/ibex_if_stage_3children.xlsx --check --strict`：通过。
- `review_test_cases/08_real_test_2/01_core_layer.xlsx --check --strict`：通过。
- `review_test_cases/08_real_test_2/02_if_stage_layer.xlsx --check --strict`：通过。
- 当前 `test.xlsx --check`：无 error，预计生成 3 个文件；保留的模板占位、位宽差异和多驱动项目数据继续产生预期 warning。
- Python 源码编译与 `git diff --check`：通过。

## 工具边界

当前环境未安装 iverilog、Verilator、ruff、pyright 或 mypy。因此 Verilog 侧采用预处理组合测试与独立静态结构审计，Python 侧采用源码编译、22 项单元测试和人工代码审查。interface 的最终编译仍需要生产项目提供对应 interface 定义。
