# XVLink 离线可视化工具

## 启动

需要 Python 3.10+，无第三方依赖。

```powershell
python .\appendix\run_designer.py
python .\appendix\run_viewer.py
```

两个脚本会选择一个空闲端口、只绑定 `127.0.0.1` 并打开默认浏览器。不需要互联网、云账号或本地管理员权限。若不希望自动打开浏览器：

```powershell
python -m appendix.server --mode designer --port 8765 --no-browser
python -m appendix.server --mode viewer --port 8766 --no-browser
```

## 集成页签设计器

1. 点“选择 XLSX”并导入。界面显示可识别 module、端口数和原始解析诊断。
2. 选择一个 TOP 和两个直接子模块。三个角色必须不同；工作簿有更多 module 时不会静默猜测。
3. 从一个端口拖到另一个端口，或依次点击两个端口建立网络。`Ctrl+点击`或在画布空白处拖出选框可多选当前可见端口，再用“将多选端口建为一网”批量连接；`Shift+拖动空白处`平移画布。搜索与方向过滤在大模块中同样有效；端口列表采用虚拟滚动窗口。
4. “生成建议”只产生建议，不修改已确认网络。每条显示得分原因、位宽 warning 和高/中/低置信度；批量接受不会自动接受位宽不同的建议。
5. 处理诊断并确认未连接端口。子模块 input 预览为接零，output/inout/interface 为空连接。
6. 预览二维集成表，再“另存为 XLSX”。有 error 时禁止导出；warning 需二次确认。导出临时文件必须通过主生成器 check 才会原子发布。

“保存工程”使用 `.xvlink.json`，保存源路径与 SHA-256 指纹、角色、稳定网络 ID、已拒绝建议和未连接确认。打开时若源文件已变，失效端点会成为 `MISSING_PORT` error，不会静默删除。撤销/重做保留最近 100 个状态；每次修改同时写入浏览器本地自动保存，崩溃后可用“恢复自动保存”找回最近状态。

可直接打开 `appendix/examples/09_version_2_demo.xvlink.json` 查看基于 V2 工作簿的 reset 扇出与子模块内部 array 网络示例。

## 信号阅读器

阅读器可打开已含集成页签的 XLSX，也可打开设计器工程。左侧树将网络分为 `TOP ↔ 子模块`、`子模块内部`和`单端/未连接`；点击网络会降低其他箭头的透明度，右侧显示所有端点、方向、位宽/形状、分类和扇出。

## 架构与数据模型

```text
browser UI (designer.js / viewer.js)
                 │ JSON over localhost
                 ▼
server.py —— 路由、静态资源、系统文件对话框
                 │
                 ▼
xvlink_core.py
  ├─ 共享 xlsx2verilog.parse_workbook()
  ├─ 建议索引（名称 / 模板 provenance）
  ├─ 连接模型与阻断规则
  ├─ .xvlink.json 持久化
  └─ OOXML 集成页签写入 → generate(check_only=True)
```

- `model.modules[].ports[]` 是从主解析器导出的只读端点，ID 为 `module:port`，带位宽维度、XLSX 数组维度、interface、模板、条件、分类和源行；普通信号生成时两类维度都位于名称左侧。
- `project.networks[]` 包含稳定 UUID 和端点 ID 列表。第一版的集成表每个网络在同一 module 中最多一个端口，超出时 `DUP_MODULE` 阻断导出。
- XLSX 写入只替换/新增集成 worksheet 和必要的 workbook relationships/content types。其他 ZIP entry 原样复制，module worksheet 不被重写。

## 测试、打包与发布

```powershell
python -m unittest tests.test_appendix -v
python -m py_compile .\appendix\xvlink_core.py .\appendix\server.py
```

没有需要锁定的 pip 包，`requirements.txt` 因此为空依赖声明。发布可直接复制整个仓库；若需单 EXE，可在可联网的构建机上使用 PyInstaller 打包 `appendix/run_designer.py` 和 `appendix/run_viewer.py`，并将 `appendix/static` 作为 data 目录包入。运行机仍不需要联网。

## 已知限制与路线图

- 第一版只导出一个 TOP 和两个直接子模块，不生成 mux、仲裁、CDC 或协议转换。
- 框选只能选中当前虚拟窗口已渲染的端口；对超大模块的跨页多选，请使用搜索后 `Ctrl+点击` 累积选择。
- 工作簿样式、合并单元格和图片由原 OOXML 保留；新集成页签使用无样式 inline string。后续可添加可选主题样式复制。
- 设计器在源文件改变后保留失效 ID 并阻断导出；后续可加入交互式端口重映射向导。
