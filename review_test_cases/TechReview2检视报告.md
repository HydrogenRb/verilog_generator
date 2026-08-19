# Tech Review 2 三轮检视报告

本报告由 `tests/run_tech_review2_review.py` 生成，覆盖新功能、历史回归和生成代码静态结构。

## 汇总

| 轮次 | 结果 | 检视点 |
|---|---|---|
| 第一轮：新功能与边界输入检视 | 通过 | 15 项通过，0 项问题 |
| 第二轮：历史功能和 real_test 回归 | 通过 | 5 项通过，0 项问题 |
| 第三轮：生成 Verilog 独立静态检视 | 通过 | 36 项通过，0 项问题 |

## 第一轮：新功能与边界输入检视

- PASS：最新 test.xlsx 无错误生成 3 个模块
- PASS：生成文件数为 3
- PASS：{{i}} 已展开 sig1
- PASS：DW_SIG1 不确定位宽使用 114
- PASS：{{j}} 已展开 sig1，畸形 valid 模板已恢复
- PASS：{{i}} 已展开 sig2
- PASS：DW_SIG2 不确定位宽使用 114
- PASS：{{j}} 已展开 sig2，畸形 valid 模板已恢复
- PASS：{{i}} 已展开 sig3
- PASS：DW_SIG3 不确定位宽使用 114
- PASS：{{j}} 已展开 sig3，畸形 valid 模板已恢复
- PASS：interface 声明和实例连接正确
- PASS：乘号已按原顺序转换为多维 packed array，且宏规范为大写
- PASS：当前样例 9 个模板宏位宽均产生明确的 114 告警
- PASS：当前 TOP 的 j 端口中未绑定 i 位宽产生 3 个明确的 114 告警

## 第二轮：历史功能和 real_test 回归

- PASS：unittest 40/40 通过
- PASS：Tech Review 1 matrix 6/6 通过
- PASS：review_test_cases\07_real_test_1\ibex_if_stage_3children.xlsx --strict 通过
- PASS：review_test_cases\08_real_test_2\01_core_layer.xlsx --strict 通过
- PASS：review_test_cases\08_real_test_2\02_if_stage_layer.xlsx --strict 通过

## 第三轮：生成 Verilog 独立静态检视

- PASS：生成文件集合与模块集合一致
- PASS：RISCV_TOP.v 模块边界唯一
- PASS：RISCV_TOP.v 括号平衡
- PASS：RISCV_TOP.v 端口列表完整且无重复
- PASS：RISCV_CORE_TEST.v 模块边界唯一
- PASS：RISCV_CORE_TEST.v 括号平衡
- PASS：RISCV_CORE_TEST.v 端口列表完整且无重复
- PASS：RISCV_CORE_TEST.v.ahb_test_3 output 已赋零
- PASS：RISCV_CORE_TEST.v.ahb_test_4 output 已赋零
- PASS：RISCV_CORE_TEST.v.ahb_test_5 output 已赋零
- PASS：RISCV_CORE_TEST.v.apb_3 output 已赋零
- PASS：RISCV_CORE_TEST.v.apb_4 output 已赋零
- PASS：RISCV_CORE_TEST.v.apb_5 output 已赋零
- PASS：RISCV_CORE_TEST.v.apb_6 output 已赋零
- PASS：RISCV_CORE_TEST.v.test_bus_sig1_valid output 已赋零
- PASS：RISCV_CORE_TEST.v.test_bus_sig2_valid output 已赋零
- PASS：RISCV_CORE_TEST.v.test_bus_sig3_valid output 已赋零
- PASS：RISCV_CORE_TEST.v.array output 已赋零
- PASS：RISCV_CORE_TEST.v.test_bus2_sig1_valid output 已赋零
- PASS：RISCV_CORE_TEST.v.test_bus2_sig2_valid output 已赋零
- PASS：RISCV_CORE_TEST.v.test_bus2_sig3_valid output 已赋零
- PASS：MEM_PHY.v 模块边界唯一
- PASS：MEM_PHY.v 括号平衡
- PASS：MEM_PHY.v 端口列表完整且无重复
- PASS：MEM_PHY.v.apb_1 output 已赋零
- PASS：MEM_PHY.v.apb_2 output 已赋零
- PASS：MEM_PHY.v.ahb_test_3 output 已赋零
- PASS：MEM_PHY.v.ahb_test_4 output 已赋零
- PASS：MEM_PHY.v.ahb_test_5 output 已赋零
- PASS：MEM_PHY.v.test_bus_sig1_valid output 已赋零
- PASS：MEM_PHY.v.test_bus_sig2_valid output 已赋零
- PASS：MEM_PHY.v.test_bus_sig3_valid output 已赋零
- PASS：RISCV_CORE_TEST 每个端口恰好连接一次
- PASS：MEM_PHY 每个端口恰好连接一次
- PASS：生成代码中不再存在命名模板占位符
- PASS：生成目录无残留临时文件

## 诊断分类

- 脚本问题：0。
- XLSX 待确认数据：12 条模板位宽无法确定，已告警并使用 114。
- 项目定义差异：样例保留 0 条 APB 位宽不匹配告警。
- 项目定义待确认：3 个 test_bus valid TOP output 同时连接 CORE/MEM output，已生成但明确告警多驱动。
- 工具边界：当前环境未安装 iverilog/verilator，第三轮使用独立静态结构检视；interface 的最终编译仍需项目提供对应 interface 定义。
