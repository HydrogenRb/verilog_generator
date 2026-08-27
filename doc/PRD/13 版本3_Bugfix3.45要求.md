# parameter的不确定
如果我在子模块中定义了一个宽度是parameter的信号，在顶层使用的时候，位宽是PARAMTER，这让我遇到了error；（参考当前的main_test.xlsx和生成代码）
我应该：在这种情况下，创建一个parameter并且给其默认数值

# parameter的NA[i]功能
当前我在parameter分类中使用NA[i]->loc_param_a之后会出现（假设我例化16次）localparam loc_param_a = 16;
但是实际上我想要的是
```
localparam [15:0][这个地方你自己定] loc_param_a = '{1,2,3,`macro1,`macro2...} //数据由用户自己填写or从i/o取
...
generate...for...
MODULE_1 #(.loc_param_a(loc_param_a[i]))...
```

# 新功能
目前我们使用module:xxxxx作为文件名，我可以写成如下从而启用新的模式，让我们的系统能以一个module生成多个不同例化名的系统；
```
module: RISC_TOP 例化名:RISC_CORE1
```
这种例化名指定方法比另一个要更加有优先级