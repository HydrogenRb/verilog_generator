当前版本进入到V3.4
# 逻辑规整化
当前，集成页签中如果NA->0和留空，会出现不一样的表现，其中一个是'0，其中一个是{parametere{1'b0}}或者{macro{1'b0}}；
我希望结果都是{parametere{1'b0}}或者{macro{1'b0}}这种（这样不会报link warning），请你将所有的都统一。

# 顶层模块互联或者单独信号的位宽
当前，顶层模块有很多互联或者因为NA生成的信号，其位宽取决于output信号或者NA信号的位宽和形状；
请你修改成（形状的判断逻辑不变）
- 如果位宽来源（子模块的生成）是macro，则按照macro 因为macro是全局的
- 如果位宽来源是parameter，则追踪其来源
- 如果来源是top用参数赋值，则位宽应该是top的参数名
- 如果来源是top用macro赋值，则位宽应该是macro
- 如果来源是top用数字，则位宽就是子模块的param名字

# parameter支持NA
现在继承页签中，parameter应该支持NA（不包括最左边那一条，那是顶层）
- 当我在parameter旁边输入NA->A的时候：在assign区域附近生成localparam来用，值选择i/o中的，如果没有找到就选114
- 当我在parameter旁边输入NA[a]->B的时候：在assign区域附近生成多维localparam，值选择例化次数
- 当我在parameter旁边输入NA->514（一个数字）的时候：在assign区域附近生成localparam并且给这个赋值

# parameter支持macro
现在，我们可以将数字或者macro写入i/o从而在赋值的时候直接使用

# 跳过模块功能
目前集成页签的模块定义是：module:xxxx
添加新功能允许我通过module:xxxx *注释*直接整个注释掉这个模块
请注意，这个注释不应该影响其他任何任何功能，就等效于在生成之后我人手 /**/注释掉；