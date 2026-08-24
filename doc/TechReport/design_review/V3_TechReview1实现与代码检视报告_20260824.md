# V3.1 TechReview1 实现与代码检视报告

日期：2026-08-24

## 1. 实现结论

`doc/PRD/9 版本3_TechReview1追加需求.md`中的主脚本需求已实现：

- 直接运行输出对齐的脚本名、V3.1、日期和联系方式；
- 文件顶部提供明文 Verilog 头配置，并以`OVERWRITE_FILE_HEADER`控制是否保留已有`file header` USER 段；
- parameter 不再生成`integer`，默认生成`localparam`；只有集成页签中显式链接的子模块参数可由实例覆盖；
- parameter 行的`位宽`可填写完整宏引用，`数值`继续用于静态匹配并生成行尾注释；
- 生成器产生的宏默认值全部改为注释参考，避免主动重定义；
- `NA->名称`创建指定名称的 wire，`NA->0/1`支持常量连接和 TOP output assign；
- 可选`模块名/例化名/例化次数`元数据表控制实例名和 generate 次数；
- 每个实例使用独立`i_gen_<实例名>`，显式次数超过索引范围时 warning；
- 自动遗漏、单端连接和 NA 占位类诊断降为 info；error/warning/info 可独立隐藏，但仍参与失败判定；
- README、功能特性表和维护指南已按当前`doc/`、`tests_script/`目录重新整理。

## 2. 样例验收

`review_test_cases/14_edge_case_test_problem/eage_case.xlsx`普通检查无 error，可生成五个模块。重点结果：

- TOP：`localparam RST_LANE = `GLB_RST_LANE`并保留匹配值`// 1`；
- `RISCV_CORE_TEST`：显式链接的`RST_LANE/CLK_LANE`为可覆盖`parameter`，其他参数仍为`localparam`；
- `ready_test_process`按指定名称声明并连接；
- 三个`test_bus2_*_valid`由`NA->1`生成 assign；
- `MEM_DAT`实例名为`PROJECT_PERSONAL_MEM_DAT`；
- `RISCV_CRG`使用`i_gen_u_riscv_crg`循环 10 次。

样例仍保留模板变量未绑定、114 占位、位宽差异和多驱动等已知 warning。因此普通`--check`预期成功，`--check --strict`预期失败；这不是 V3.1 功能错误。

## 3. 代码检视

本轮重点检查了以下不变量：

1. 参数局部性：未出现在集成`parameter`行的参数不进入实例`#(...)`，子模块默认值不会按同名从 TOP 或兄弟模块隐式继承。
2. 参数链接：TOP↔子模块和子模块↔子模块都使用 TOP localparam；后者自动创建时有 info。
3. 常量方向安全：常量只直接连接 input；子模块 output 在常量网络中开路，避免非法连接到常量表达式。
4. 例化安全：每个 generate 使用独立 genvar；显式次数不被静默截断，可能越界时给出 warning。
5. 写入安全：文件头覆盖开关只影响`file header`，其他 USER 段继续走原有损坏保护和全量原子写入。
6. 诊断安全：显示开关不删除内部诊断，不会绕过 error 或 strict warning。
7. 向后兼容：没有参数连接分类或实例元数据列的旧工作簿继续使用默认 localparam、`U_<MODULE>`和自动次数。

## 4. 自动回归

新增`tests_script/test_version3_review1.py`，覆盖主验收工作簿、子模块参数互连自动 localparam、显式次数越界、自定义实例名、文件头保留/覆盖、启动标识和诊断显示开关。旧测试已迁移到 V3.1 的 local parameter、注释宏和独立 genvar 预期；归档后残留的`tests`包引用已改为`tests_script`。

附录可视化工具已不在当前仓库，旧`test_appendix.py`在模块缺失时明确 skip，不阻断主生成器测试发现。

## 5. 已知边界

- `NA->名称`用于子模块互连区；TOP 连接区仅允许`NA->常量`。
- 显式例化次数即用户意图，超过索引范围只 warning，不自动改小。
- 注释宏不会为编译环境提供定义，真实工程必须从统一宏头文件或编译参数提供。
- 多维 packed 与 interface 仍要求 SystemVerilog 模式。
