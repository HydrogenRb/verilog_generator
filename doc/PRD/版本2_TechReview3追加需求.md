# 声明
本次修改全部围绕主项目，即xlsx2verilog.py的信息；
修改的时候记得同步修改README.md和doc/TechReport/代码结构与维护指南.md；
在代码结构文档中，说明当前脚本的架构；
新建了一个13_test_for_techreview3的测试用例

# 新增用户字段
在module声明之后、第一个instantiation之前、每一个instantiation之后都加入一个用户段，如
```verilog
/*USER CODE BEGIN before statement*/

/*USER CODE END   before statement*/
... //真实代码
/*USER CODE BEGIN before MODULE*/

/*USER CODE END   before MODULE*/
MODULE U_MODULE... //真实代码
```
当重新生成的时候，用户的代码不应该被覆盖

# 新增生成逻辑
内部互联信号也可以生成generate模块，参考RISCV_CRG这个模块中的信号可以连接到xx[i]，因为我的链接出现了NA[i]，所以应该生成generate模块；同时，因为是NA，所以别忘了生成对应的注释和wire

# 更多模板操作
模板操作可以使用z=0:31这样的动作来节约时间，参考信号high_clk_after_pll_{{z}}

# 信号名操作
当前，信号名被你统一变成了小写，但是信号名应该大小写敏感，100%按照用户的输入操作；

# 声明对齐
当前，如果我在i/o输入io的时候会生成inout，但是inout后面有一个wire，去掉这个wire

# Parameter
- 当我使用分类Parameter的时候，应该支持在位宽的部分使用宏，换句话说我希望能变成这样的感觉
```verilog
parameter LANE_NUM = `GLB_PARAMETER;
parameter DW = 8;
```

# Debug辅助
当前，warning，error和info是按照时间顺序生成，请你将这三个分类显示，并且显示颜色