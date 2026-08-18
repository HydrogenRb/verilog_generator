# 声明
本次修改全部围绕主项目，即xlsx2verilog.py的信息；
修改的时候记得同步修改README.md和doc/TechReport/代码结构与维护指南.md；
新建了一个10_special_case的测试用例，

# 当前情况


# 小修改
1.assign已经想左靠齐，声明module的部分也应该向左靠齐
```verilog
assign a;
PHY_SUB U_PHY_SUB(//这一行没有空格
    input xxx;//这只有4个空格
) 
```
2.模型的名字应该是英文大写字母；页签和module名不限制大小写；总结是在excel中放宽大小写限制，在生成Verilog的时候只有信号是小写，例化的时候用大写，模块也用大写
3.