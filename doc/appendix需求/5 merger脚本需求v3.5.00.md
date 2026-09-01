当前，脚本已经进入到了版本v3.5.00
当前版本的首要任务是修改merger从而保证代码的正确性

# 追加功能：支持注释generate
对于某一个generate block，你需要支持对于这种注释的保留
(当前，真实生产环境中，genvar已经全部变成了i)
```verilog
/*USER CODE END  before xxx_RX*/
//genvar i; //USER: we have a huge generate block
//generate //USER: we have a huge generate block
for (...)
    xxx_RX #()...
end
//endgenerate //USER: we have a huge generate block
/*USER CODE BEGIN after xxx_RX*/
```

# debug对于多个信号的功能
当前，我在下列文件夹构建出来了一个具体的损坏用例，展示了这个bug的细节：
我有代码C:\Users\HydrogenRb\HydrogenRb\Work\tech\Agent\8_14\appendix\tests\real_case\temp\riscv_top.v希望覆盖给work_rtl，结果覆盖的结果是错的，覆盖之前是C:\Users\HydrogenRb\HydrogenRb\Work\tech\Agent\8_14\appendix\tests\real_case\temp\riscv_top.v.xlsx2verilog_merger_backup\20260901_140301_561400\riscv_top.v覆盖之后是C:\Users\HydrogenRb\HydrogenRb\Work\tech\Agent\8_14\appendix\tests\real_case\work_rtl\riscv_top.v。
脚本对于同一个数据多次出现的情况支持的非常差
从用户侧一个经典行为是：
1.制作表格的时候将某一个top的output留成全是空位
2.生成的代码中有output wire [8 -1:0] aaa;和assign aaa = {8{1'b0}};
3.用户comment掉了后者，然后在USER CODE中设置了assign aaa = xxx；
merger对于这种行为不支持

# 功能修改
- 去掉当//USER:匹配到多条新声明时的error，而是出现warning
- 去掉claimed_positions的修改，我不希望阻塞
- 对于报错，至少应该显示行数

# 追加功能：自动bcompare
merger提供一个config位，如果设置成true，则merge之后自动打开bcompare（项目使用环境是Linux）左边是备份的旧代码，右边是生产环境中新代码；对于多个文件同时赋值的场景，可以使用&从而打开多份