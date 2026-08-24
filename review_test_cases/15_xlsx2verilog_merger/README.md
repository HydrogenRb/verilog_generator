# xlsx2verilog_merger 真实覆盖用例

自动回归位于`appendix/tests/test_xlsx2verilog_merger.py`，测试期间只在系统临时目录操作：

1. 使用`14_edge_case_test_problem/eage_case.xlsx`生成旧版本项目；
2. 在旧 TOP 的`before statement` USER 段加入一行人工代码；
3. 复制 XLSX，并把集成页签中`RISCV_CRG`的例化次数从`10`调整为`9`；
4. 使用主脚本生成新版本；
5. 使用独立`xlsx2verilog_merger`覆盖旧项目；
6. 验证 generate 次数和内部聚合 wire 的首维更新为`9`，且人工代码原样保留；
7. 验证旧项目把生成 wire 改成 reg 后，merger 仍按`模块名.信号名`保留 reg，而位宽等结构采用新版本；
8. 验证被覆盖文件存在备份，源 XLSX 和仓库内生成物均未被测试修改。

运行：

```powershell
py.exe -m unittest discover -s appendix\tests -v
```
