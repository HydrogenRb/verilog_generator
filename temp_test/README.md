# 变量值扩散验收样例

- `variable_diffusion_before.xlsx`：`WIDTH` 缺少数值，`--check` 必须返回 error。
- `variable_diffusion_after.xlsx`：对副本执行 `WIDTH=(3+5)` 扩散后的结果，`--check` 无 error。
- `backup/`：扩散前由主脚本自动创建的带时间戳原始副本；其内容应与扩散前的 `variable_diffusion_after.xlsx` 完全一致。

复验命令：

```powershell
python -B .\xlsx2verilog.py .\temp_test\variable_diffusion_before.xlsx --check
python -B .\xlsx2verilog.py .\temp_test\variable_diffusion_after.xlsx --check
```

第一条命令预期返回码为 `2`，第二条预期返回码为 `0`。
