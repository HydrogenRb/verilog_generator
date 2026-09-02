当前版本是3.5.01

# xlsx2verilog_merger.py 同名实例端口错误匹配修复任务

## 1. 任务目标

请修复当前 `xlsx2verilog_merger.py` 中 `//USER:` 实例端口连接的错误匹配问题。

当前脚本在同一个 Verilog module 中，如果不同实例存在同名端口，例如：

```verilog
U_RISCV_CORE_TEST (
    ...
    .test_rx (test_rx) //USER: not use
);

GEN_PHY_U (
    ...
    .test_rx (test_rx[0][0]), //USER: not change
    ...
);
```

合并时会把两个不同实例中的 `.test_rx` 视为同一个对象。

结果可能发生：

```text
旧文件 U_RISCV_CORE_TEST.test_rx
旧文件 GEN_PHY_U.test_rx

          ↓

都匹配到新文件中的第一个 .test_rx

          ↓

后一条 //USER: 覆盖前一条

          ↓

最终生成的 Verilog 中：
GEN_PHY_U 的用户修改被错误写到了 U_RISCV_CORE_TEST
```

这是一个严重的 silent corruption 问题。

修复后必须确保：

> 不同实例中的同名端口不会互相覆盖。

---

# 2. 本次真实复现案例

输入涉及三份文件：

```text
riscv_top_from_python.v
riscv_top_before.v
riscv_top_after.v
```

含义：

```text
from_python
    = Python 新生成的 Verilog

before
    = 当前生产代码，其中包含用户 //USER: 修改

after
    = 当前 merger 合并出来的错误结果
```

---

## 2.1 before 中存在两个不同实例的 `.test_rx`

第一个属于：

```text
U_RISCV_CORE_TEST
```

内容类似：

```verilog
.test_rx (test_rx) //USER: not use
```

第二个属于：

```text
GEN_PHY_U
```

内容类似：

```verilog
.test_rx (test_rx[0][0]), //USER: not change
```

虽然二者的 port name 都是：

```text
test_rx
```

但它们实际上是两个完全不同的实例端口：

```text
riscv_top
├── U_RISCV_CORE_TEST
│   └── test_rx
│
└── GEN_PHY_U
    └── test_rx
```

正确的 identity 至少不能只使用：

```text
(module_name, port_name)
```

---

# 3. 当前代码的根因

## 3.1 PortConnection 没有实例信息

当前 `PortConnection` 大致只保存：

```python
class PortConnection:
    module_name: str
    port_name: str
    statement_start: int
    statement_end: int
    line_number: int
```

`parse_port_connections()` 找到：

```verilog
.test_rx(...)
```

后只记录：

```python
PortConnection(
    current_module,
    connection_match.group("name"),
    ...
)
```

因此：

```verilog
U_RISCV_CORE_TEST (
    .test_rx(...)
);
```

和：

```verilog
GEN_PHY_U (
    .test_rx(...)
);
```

最终都会退化成：

```text
(riscv_top, test_rx)
```

实例 identity 完全丢失。

---

## 3.2 //USER: port 解析同样丢失实例信息

`parse_user_owned_lines()` 对：

```verilog
.test_rx(...) //USER:
```

生成的 UserOwnedLine 当前等价于：

```python
UserOwnedLine(
    kind="port",
    module_name=current_module,
    key="test_rx",
    ...
)
```

这里的：

```text
key
```

只是 port name。

它既没有：

```text
instance_name
```

也没有：

```text
occurrence
```

所以 before 中两个 `.test_rx //USER:` 会得到相同 identity。

---

# 4. 最关键的错误代码

当前新文件端口索引类似：

```python
connections_by_key: dict[
    tuple[str, str],
    list[PortConnection]
] = {}

for item in new_connections:
    connections_by_key.setdefault(
        (item.module_name, item.port_name),
        []
    ).append(item)
```

因此两个 `.test_rx` 都进入：

```python
connections_by_key[
    ("riscv_top", "test_rx")
]
```

得到：

```text
[
    U_RISCV_CORE_TEST.test_rx,
    GEN_PHY_U.test_rx,
]
```

随后：

```python
select_candidate(...)
```

在出现多个 candidate 时，当前实现只是 warning，然后：

```python
return candidates[0]
```

这就是问题核心。

当前代码实际上并没有真正进行“结构上下文选择”。

它只是：

```text
多个候选
    ↓
选第一个
```

---

# 5. 本次 test_rx 是如何损坏的

假设：

```text
before 第一个 test_rx
= U_RISCV_CORE_TEST.test_rx

before 第二个 test_rx
= GEN_PHY_U.test_rx
```

新文件中同样有：

```text
new test_rx #0
new test_rx #1
```

处理第一条 old USER 时：

```text
候选 = [new#0, new#1]

select_candidate()
    ↓
new#0
```

所以：

```text
old#0 → new#0
```

暂时正确。

处理第二条 old USER 时：

```text
候选仍然 = [new#0, new#1]

select_candidate()
    ↓
仍然 new#0
```

所以：

```text
old#1 → new#0
```

错误。

由于 replacement 使用：

```python
(candidate_start, candidate_end)
```

作为 dictionary key：

```python
replacements[span] = ...
```

因此第二条 old USER 会覆盖第一条：

```text
new#0 ← old#1
```

而：

```text
new#1
```

从始至终没有被覆盖。

最终产生错误 after。

---

# 6. 必须首先修复的安全问题

无论最终采用哪种 identity 方案，都必须修改：

```python
if len(candidates) > 1:
    warning(...)

return candidates[0]
```

这种行为。

## 禁止继续：

```text
存在多个候选 → 自动选择第一项
```

因为这种行为会生成“语法看似正常、语义已经损坏”的 Verilog。

这是比直接报错更危险的问题。

如果无法可靠地区分多个 candidate：

```python
raise MergeError(...)
```

也比：

```python
return candidates[0]
```

安全。

原则：

```text
宁可拒绝不确定的合并
也不要猜测后继续写生产代码
```

---

# 7. 推荐修改方案

建议分两个层级实现。

---

## 第一层：必须实现 occurrence 匹配

这是本次 bug 的最低要求，也是对现有架构侵入最小的修复。

将实例端口 identity 从：

```text
(module_name, port_name)
```

升级为：

```text
(module_name, port_name, occurrence)
```

其中 occurrence 是：

> 同一个 module 中，同名 named port association 出现的序号。

例如：

```verilog
U_RISCV_CORE_TEST (
    .test_rx(...)
);

GEN_PHY_U (
    .test_rx(...)
);
```

解析成：

```text
riscv_top.test_rx#0
riscv_top.test_rx#1
```

这样：

```text
old test_rx#0 → new test_rx#0
old test_rx#1 → new test_rx#1
```

不会互相覆盖。

---

# 8. PortConnection 建议修改

当前：

```python
@dataclass(frozen=True)
class PortConnection:
    module_name: str
    port_name: str
    statement_start: int
    statement_end: int
    line_number: int
```

至少改为：

```python
@dataclass(frozen=True)
class PortConnection:
    module_name: str
    port_name: str
    occurrence: int
    statement_start: int
    statement_end: int
    line_number: int
```

---

# 9. parse_port_connections() 修改要求

在函数内部增加：

```python
occurrences: dict[tuple[str, str], int] = {}
```

遇到 named port：

```python
port_name = connection_match.group("name")

occurrence_key = (
    current_module,
    port_name,
)

occurrence = occurrences.get(
    occurrence_key,
    0,
)

occurrences[occurrence_key] = occurrence + 1
```

创建：

```python
PortConnection(
    current_module,
    port_name,
    occurrence,
    ...
)
```

注意：

occurrence 必须在整个当前 module 范围内按：

```text
module + port_name
```

独立计数。

进入下一个 module 时应自然形成新的 identity 空间。

---

# 10. parse_user_owned_lines() 同样必须记录 occurrence

`UserOwnedLine` 当前已经有：

```python
occurrence: int | None = None
```

请复用这个字段。

目前 port 分支类似：

```python
UserOwnedLine(
    "port",
    current_module,
    connection_match.group("name"),
    None,
    statement,
    line_number,
)
```

需要变成：

```python
UserOwnedLine(
    "port",
    current_module,
    port_name,
    None,
    statement,
    line_number,
    occurrence=occurrence,
)
```

因此 `parse_user_owned_lines()` 也需要维护：

```python
port_occurrences: dict[
    tuple[str, str],
    int
] = {}
```

重要：

即使某个 port connection 没有 `//USER:`，也要考虑 occurrence 的正确计数。

也就是说 occurrence 必须对应：

> 它在完整 Verilog 结构里的实际出现顺序。

不能只对带 `//USER:` 的行计数。

否则：

```text
新文件 occurrence
```

和：

```text
旧文件 USER occurrence
```

可能错位。

---

# 11. connections_by_key 修改

不要再使用：

```python
dict[
    tuple[str, str],
    list[PortConnection]
]
```

作为主要精确匹配。

至少改成：

```python
dict[
    tuple[str, str, int],
    PortConnection
]
```

例如：

```python
connections_by_key = {}

for item in new_connections:
    key = (
        item.module_name,
        item.port_name,
        item.occurrence,
    )

    connections_by_key[key] = item
```

查找：

```python
key = (
    old_line.module_name,
    old_line.key,
    old_line.occurrence or 0,
)

connection = connections_by_key.get(key)
```

找不到时：

```python
raise MergeError(...)
```

不要静默选择同名第一项。

---

# 12. 更推荐的长期方案：增加 instance_name

occurrence 可以解决当前 test_rx 问题，但 occurrence 仍然有结构漂移风险。

例如旧文件：

```text
test_rx#0 → instance A
test_rx#1 → instance B
```

新版本如果前面新插入一个实例：

```text
test_rx#0 → 新实例 X
test_rx#1 → instance A
test_rx#2 → instance B
```

纯 occurrence 就会错位。

因此更加正确的 identity 应该是：

```text
module_name
+
instance_name
+
port_name
```

例如：

```text
(riscv_top, U_RISCV_CORE_TEST, test_rx)

(riscv_top, GEN_PHY_U, test_rx)
```

这两个天然不会冲突。

---

# 13. 推荐最终 PortConnection

如果在现有 line-oriented parser 中能够可靠识别 instance：

```python
@dataclass(frozen=True)
class PortConnection:
    module_name: str
    instance_name: str | None
    port_name: str
    occurrence: int
    statement_start: int
    statement_end: int
    line_number: int
```

推荐同时保留：

```text
instance_name
occurrence
```

---

# 14. 推荐匹配优先级

最终建议：

```text
优先级 1
module + instance_name + port_name

        ↓ 找不到

优先级 2
module + port_name + occurrence

        ↓ 找不到

MergeError
```

不要：

```text
module + port_name
        ↓
多个 candidate
        ↓
选第一个
```

---

# 15. 不要求引入完整 Verilog parser

当前工程本身采用 conservative line-oriented recognizer。

本次修复应尽量保持这一设计，不要求为了这个 bug 引入复杂的 Verilog AST parser。

如果可靠提取 instance name 会显著增加复杂度，可以先：

```text
实现 occurrence 修复
+
禁止 ambiguous candidate 自动选择第一项
```

这是本任务的最低可接受实现。

然后把 instance-aware matching 作为增强。

---

# 16. 特别注意 generate 场景

真实复现中的第二个 `.test_rx` 位于：

```text
generate
```

结构中的实例：

```text
GEN_PHY_U
```

修复不能假设：

```text
named port association
```

只出现在 module 的一级范围。

必须允许：

```verilog
generate
    ...
    SOME_MODULE GEN_PHY_U (
        ...
        .test_rx(...)
    );
endgenerate
```

occurrence 方案天然可以覆盖这种情况。

如果实现 instance_name 解析，也必须支持 generate 内实例。

---

# 17. 不要改变 //USER: 行的原始文本

当前 merger 的目标之一是保留完整用户行，例如：

```verilog
.test_rx (test_rx[0][0]), //USER: not change
```

修复 identity 逻辑时：

不要重新格式化为：

```verilog
.test_rx(test_rx[0][0]),
```

也不要重新生成 comment。

应该继续整体保存：

```python
old_line.statement
```

再替换正确 candidate 的完整 span。

本次只修改：

```text
候选定位
```

不要无关改变：

```text
原始用户文本保留方式
```

---

# 18. 注意逗号问题

本次错误还有一个副作用。

例如：

```verilog
U_RISCV_CORE_TEST (
    ...
    .test_rx(test_rx)
);
```

最后一个 port 没有逗号。

另一个实例：

```verilog
GEN_PHY_U (
    ...
    .test_rx(test_rx[0][0]),
    .test_tx(...)
);
```

这里 `.test_rx` 有逗号。

当前错误匹配会直接把后一条完整 old statement 搬到前一个实例中：

```verilog
.test_rx(test_rx[0][0]),
);
```

因此不仅信号语义错误，还可能形成非法 trailing comma。

修复后必须保证：

> old user-owned line 只能回填到它对应的实例/occurrence。

不要尝试通过“自动删除逗号”掩盖 identity 错误。

根因必须在匹配层解决。

---

# 19. 对 ambiguous candidate 的新规则

请修改通用 `select_candidate()` 的行为，或者至少修改 port matching。

推荐规则：

### 唯一候选

```text
1 candidate
→ 使用
```

### 0 个候选

```text
0 candidate
→ MergeError
```

### 多个候选

如果已经通过精确 identity：

```text
instance + port
```

或：

```text
port + occurrence
```

则原则上不应出现多候选。

如果仍然出现：

```text
>1 candidate
→ MergeError
```

不要自动取：

```python
candidates[0]
```

---

# 20. 修改时不要破坏其他现有功能

需要保持当前功能：

```text
USER CODE BEGIN/END 区域保护
wire/reg 类型保留
parameter/localparam 类型保留
assign //USER: 保留
named port //USER: 保留
genvar/generate/endgenerate //USER: 保留
文件备份
事务式写入/回滚
check-only
```

本次修改应该尽量限制在：

```text
PortConnection
parse_port_connections()
parse_user_owned_lines()
preserve_user_owned_lines()
```

及必要的辅助结构。

---

# 21. 必须增加回归测试

请为这次 bug 增加自动测试。

---

## Test 1：两个不同实例存在相同 port

before：

```verilog
module top;

MOD_A U_A (
    .test_rx(old_a) //USER: keep A
);

MOD_B U_B (
    .test_rx(old_b) //USER: keep B
);

endmodule
```

new：

```verilog
module top;

MOD_A U_A (
    .test_rx(new_a)
);

MOD_B U_B (
    .test_rx(new_b)
);

endmodule
```

expected：

```verilog
module top;

MOD_A U_A (
    .test_rx(old_a) //USER: keep A
);

MOD_B U_B (
    .test_rx(old_b) //USER: keep B
);

endmodule
```

必须保证：

```text
old_a 不会跑到 U_B
old_b 不会跑到 U_A
```

---

## Test 2：同名 port 中只有第二个带 USER

before：

```verilog
MOD_A U_A (
    .test_rx(a)
);

MOD_B U_B (
    .test_rx(old_b) //USER:
);
```

new：

```verilog
MOD_A U_A (
    .test_rx(new_a)
);

MOD_B U_B (
    .test_rx(new_b)
);
```

expected：

```verilog
MOD_A U_A (
    .test_rx(new_a)
);

MOD_B U_B (
    .test_rx(old_b) //USER:
);
```

这个测试很重要。

它可以验证 occurrence 不能只统计：

```text
带 //USER: 的 port
```

必须统计完整结构中的所有同名 port。

---

## Test 3：generate 中同名 port

```verilog
MOD_A U_A (
    .test_rx(old_a) //USER:
);

generate
    if (...) begin
        MOD_B U_B (
            .test_rx(old_b) //USER:
        );
    end
endgenerate
```

必须分别正确保留。

---

## Test 4：同名端口但不同 module

```verilog
module top_a;
    ...
    .test_rx(...)
endmodule

module top_b;
    ...
    .test_rx(...)
endmodule
```

两个 module occurrence 必须互不影响。

---

## Test 5：不能再 silent 选择 candidates[0]

人工构造无法可靠区分的 ambiguous case。

期望：

```text
MergeError
```

而不是：

```text
warning + 继续合并
```

---

# 22. 使用真实 riscv_top 做回归

完成修改后，使用：

```text
riscv_top_from_python.v
riscv_top_before.v
```

重新运行 merger。

生成结果必须满足：

### U_RISCV_CORE_TEST

保留 before 中对应的：

```verilog
.test_rx (test_rx) //USER: not use
```

### GEN_PHY_U

保留 before 中对应的：

```verilog
.test_rx (test_rx[0][0]), //USER: not change
```

两者不能互换。

---

# 23. 验收重点

修改完成后请检查：

```text
1. 同一个 module 中多个同名 port 不再全部匹配第一项

2. 两个 test_rx 分别回到各自原来的实例

3. replacements 不再发生：
   old USER A
   old USER B
   同时覆盖同一个 candidate span

4. 如果匹配仍然有歧义，必须阻止合并

5. 不破坏其他 //USER: / USER CODE 功能

6. 增加针对该 bug 的自动测试
```

---

# 24. 最低可接受修复

如果暂时不实现 instance_name parser，则至少完成：

```text
PortConnection 增加 occurrence

parse_port_connections()
按 module + port_name 对所有 port connection 计数

parse_user_owned_lines()
按相同规则计算旧 USER port 的 occurrence

匹配键改成：
(module_name, port_name, occurrence)

多候选不再 candidates[0]
```

这已经可以修复当前真实 `test_rx` 案例。

---

# 25. 更推荐的完整修复

如果当前生成 Verilog 的实例格式稳定，可以进一步实现：

```text
(module_name, instance_name, port_name)
```

作为 primary identity。

并使用：

```text
(module_name, port_name, occurrence)
```

作为 fallback。

推荐模型：

```text
Primary:
module + instance + port

Fallback:
module + port + occurrence

Ambiguous:
MergeError
```

---

# 26. 最终输出要求

请完成以下内容：

1. 修改 merger Python 代码。
2. 添加或修改自动测试。
3. 用真实 `riscv_top_from_python.v + riscv_top_before.v` 验证。
4. 确认新生成结果中两个 `.test_rx` 均正确。
5. 给出简短修改说明：

   * 根因
   * 修改的数据结构
   * 新的匹配 identity
   * fallback 逻辑
   * 新增测试
6. 不要只通过修改 `riscv_top_after.v` 来修结果，必须修 merger 算法本身。
7. 不要通过特殊判断字符串 `"test_rx"` 来打补丁，此问题必须作为通用的“不同实例同名端口”问题解决。
