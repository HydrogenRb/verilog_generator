# 声明
本次修改全部围绕主项目，即xlsx2verilog.py的信息；
修改的时候记得同步修改README.md和doc/TechReport/代码结构与维护指南.md；
新建了一个12_test_for_techreview2的测试用例

# 新增分类
当分类是parameter的时候，自动定义当前.v文件的parameter，参考新的测试用例的RISCV_CORE_TEST的parameter部分。

# 新增连接方法
信号可以连接到NA，当我们检查到信号连接到NA的时候(比如RISCV_CORE_TEST的ready_to_process，和MEM_PHY的need_to_solve)需要生成如下代码(包含声明，拉出，和加入TODO让人类后期去做)
```verilog
reg  [DW_sig3 -1:0] ready_to_process;
RISCV_CORE_TEST #(
    .UID_SIZE (UID_SIZE),
    .DW_sig3  (DW_sig3 ),
    ...
) U_RISCV_CORE_TEST (
    ...
    .ready_to_process (ready_to_process ); //TODO:本信号期望有逻辑功能，请完成
    ...
)
```

# 审美对齐
生成generate的时候[i]应该对齐，比如我能看到这样的
```verilog
.in1     (in1     [i]),
.Sig_in1 (Sig_in1 [i]),
```

# 语法错误
生成generate的时候，genvar i应该在generate之前

# 命名规范
- 当前，当我在一个集成页签中塞入多个子模块的时候，会出现这种信号w_xxxx_to_yyy；但是我希望所有子模块互相的链接名字都是w_xxxx。
- 当前，我们生成的代码的名字应该是小写，比如mem_phy.v

# 自动识别范围
对于位宽不匹配的情况，允许通过方括号给值，比如新的test中的RISCV_TOP的n_rst和clk均有5，当我在顶层使用n_rst[0]连接mem_phy的n_rst的时候，在mem_phy中应该出现
```verilog
    .n_rst (n_rst[0]),
```
的情况

# debug辅助
在显示宏不匹配的时候，报错需要展示在哪个文档中是A，在哪个文档中是B