merger需要和主脚本使用相同的版本号，将主脚本和这个脚本的版本都改成v3.31
# 对于//assign的支持
如果我有这样的代码
```verilog
//assign sig_a //user: assign later
```
请你不要覆盖

# 更多位宽的支持
如果我有代码
```verilog
//input wire sig_a //user: not use
```
请你不要覆盖

# 端口替换
对于代码
```
.sigA   (temp_w_sig_a), //user: change the input source
//.sigB    (temp_w_sigb)  //user： not use
```
其中sigA和sigB是key，这种情况不要覆盖

# 覆盖历史
对于每一条覆盖或替换都打印简单的log，保证用户可见性

# 备份
备份应该在newcode的部分，而不是oldcode的部分（old code的文件夹是生产环境）