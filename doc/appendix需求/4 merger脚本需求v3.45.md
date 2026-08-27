# 当前版本配合SLLC解BUG
实现对于
```verilog
//localparam aaa = 1 //USER:no change
//parameter = 70 //USER: no change
```
的支持，让//user能保护localparam和parameter

# 更详细的指示
在脚本中展示更多细节说明如何设置默认地址，给一两个例子

# Y/N确认
进入到覆盖or更新模式之后，每一个文件的更新都弹出一个Y/N来最后确认

# 不相干代码的无视
如果我需要用a.v去更新，递归搜索的时候见到了很多个same_b.v都不要管，只关注是不是看到了a.v

# 结束时的确认
结束的时候展示生产文件和路径，记得有换行；用途是方便用户直接复制地址去对比和打开看看