# 生成说明

本目录的 4 个 Verilog 文件由 `review2case.xlsx` 的临时副本生成。生成前仅执行了两次变量值扩散：

1. `` `APB_1 = 4``；
2. `` `LANE_NUM = 5``。

原始 `review2case.xlsx` 保留两个跨模块同名宏冲突，直接 `--check` 应返回 error；未为了生成本目录而改写原件。该输出用于检视 `dft_test_en[0]`、`MEM_DAT` generate、大小写规范、实例缩进和多维范围格式。
