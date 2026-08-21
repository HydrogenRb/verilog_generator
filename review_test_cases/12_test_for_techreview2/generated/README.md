# TechReview2 V2 生成结果

原始`techreview2version2.xlsx`刻意保留四组跨页签宏冲突，用于验证带来源的错误诊断，因此原件不会直接生成 Verilog。

本目录由测试流程在临时副本上统一以下数值后生成，原始 XLSX 未修改：

- `` `RST_LANE = 5``
- `` `CLK_LANE = 5``
- `` `APB_1 = 4``
- `` `LANE_NUM = 5``

生成物用于检视显式 parameter、NA 占位信号及 TODO、常量 bit select、generate 语法、`[i]`对齐、内部 wire 命名和小写文件名。
