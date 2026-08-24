# 版本明确
脚本中添加运行的时候自动生成以下信息（记得对齐）
                         CustomScipt xlsx2verilog
                         Version V3.1
                          2026.8.24
                         Contact xxx-xxxx in case
# 生成头信息
脚本中让用户可以明文设置自己想要生成一些什么在开头
```verilog
//******************
// Design by: aaa
// Project name: bbb
//
//******************
module ...
```
脚本中应该有一个overwrite选项，能让用户自由选择“是否把当前的开头信息当成USER段不覆盖”
# 新特性：parameter支持宏定义
我们要在parameter中使用宏定义
|分类|端口名|位宽|数值|
|---|---|---|---|
|parameter|COM_LANE_NUM|`GLB_LANE_NUM|3|
这样会生成
```verilog
parameter COM_LANE_NUM = `GLB_LANE_NUM; // 3
```
在这个例子中，使用3去进行位宽匹配什么的。
# 特性修改：parameter中的integer
去掉parameter的integer
不要下面代码的integer
```verilog
parameter integer xxx = 114;
```
# parameter的传递
当前我们默认所有的parameter都是能被上层模块改变；
但是我们现在需要默认所有parameter都是local的，需要顶层赋值的parameter需要在excel表格中显式地链接
参考14_edge_case_test_problem中的表格。
请注意，对于parameter分类无论是top链接到子模块，还是子模块之间互相连接，我们都需要在顶层模块中使用使用一个local_parameter，自动创建local parameter的时候需要提示info
# 特性修改：宏的生成
生成的任何macro的定义请注释掉，避免真实项目的重定义。
# 刷新文档
当前我将文档全部归档了一下，请你一句当前的文件夹分类，重新归类文档，重点修改TechReport下的两个.md和readme
# genvar的名字
将genvar的i改成i_gen_xxx（生成的名字）避免i重复定义
# 支持赋0
对于top的信号test_bus2_{{j}}_valid，因为后面使用了NA->1，所以不会出现名字，而是给其赋1
# 新特性：支持NA额外给名字
参考：NA->ready_test_process应该生成
```verilog
wire [这个你自己看] ready_test_process
...
.ready_to_process(ready_test_process)
```
这样支持自定义名字
# 新特性：自定义例化名和例化数量
参考文档中出现的新的列模块名，例化名，例化次数；
- 将generate block的数量改成例化次数
- 如果这个数过大，可能有超过index的风险（比如当前的例子中）输出一个warning
记得考虑没有这些列的情况，使用默认值和U_xxx这样的例化名
# 新特性：warning和info
对于按“未连接处理”这个warning全部按照降级成info
# 新特性：可配置的warning和info
对于warning info和error，在python文档的User configuration开始加入开关：能独立开关每一个info warning什么的是否显示（我的理解是：在reporter这个function中加几个if的情况）