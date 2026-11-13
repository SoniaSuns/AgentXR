# 面包板几何与连接证据

这条工作流先定位孔，再让人确认端点，以确定性规则计算节点；Codex 解释结果。当前不是无人监督的照片转网表：没有训练过的引脚检测器，颜色候选只是绝缘层区域的长轴端部，弯线、交叉、遮挡和黑色接头仍须人工确认。模型不得把颜色候选当成实际入孔点。

## 启动与使用

可选依赖：`python3 -m venv .venv`，然后 `.venv/bin/python -m pip install -r requirements-vision.txt`。`start.command` 优先使用项目 `.venv`。本次工作区的 `.venv` 基于已安装 OpenCV 4.11 / numpy 2.2.3 的本地 Python 环境创建，使用 system-site-packages；其他机器请按上述命令独立安装。普通相机分析不强制依赖 OpenCV。

1. 重启服务并刷新页面，展开“面包板：高清标定、端点与连接验证”。选择正确列数和编号方向；相机画面不要镜像。电池盒放板外，接头不遮孔，尽量俯拍、不同线用不同颜色。若 a 行实际在下，应旋转板子或使用与本工具一致的自定义编号，不能把工具标签直接等同于板上印字。
2. 确认板型，冻结高清画面。软件取高清副本，若没有则使用实际采集图，不做虚假的超分辨率。63 列板至少需要约 1240px 的有效孔格宽度；“占画面 2/3”只是取景建议，最终看最小原图孔距是否达到 20px。
3. 手动模式依次点击 `a1 → a末列 → j末列 → j1 → e1 → f末列` 的孔中心。前四点确定单应变换，后两点独立复核。不是点塑料外壳四角。可切换原始像素显示并滚动。错点可撤销。
4. 校验要求：孔格完整入镜；最小源图孔距 ≥20px；复核误差 ≤max(2px, 0.25孔距)；至少55%的预测孔位存在局部暗孔纹理。阈值是工程拒绝规则，尚未经过此硬件上的统计标定，不能视为准确率保证。重复孔格有一整列偏移歧义，因此仍需要人工确认起始孔与方向。
5. 通过后输入稳定端点编号（如 `W1.1`、`R1.1`、`LED1.A`），点击原图实际入孔处。最近孔距离须 ≤0.35孔距，且与第二近孔距离差 ≥0.25孔距。遮住的脚保持未知。几何定位通过也不证明金属针已插好。
6. 选两端，明确确认它们是同一根导线，才合并节点。电阻/LED两脚作为独立端点，不可按导线合并。颜色从不自动决定极性或同线身份。
7. 添加目标端点对的“应相通/应隔离”，查看确定性比对；可以导出 JSON。这里只比较给定的节点关系，不做任意目标电路的图同构或自动改线规划。
8. 断电后用万用表通断档确认关键节点，录入导通、不导通或不清楚。软件仅保存用户报告，不控制万用表或 Arduino。几何与测量冲突时提示复核，不给出确定改线指令。电阻或 LED 路径不适合仅凭通断蜂鸣判断，应按具体元件进行相应测量。
9. 点击“用此标定帧分析”。服务端重新校验这张图，向 Codex 发送原图、孔格编号图和连接证据，跳出旧的细节检查。普通电子电路分析也优先使用已有高清副本；运行指标包含实际输入分辨率和来源。

标定、端点和测量绑定 JPEG SHA-256。新冻结帧、板型变更、切换会话会清除临时数据；不自动沿用移动相机上的旧单应变换。改变电路后必须冻结新帧并重新确认。分析成功的连接证据随会话记录保存；独立标定还可以导出 JSON。LED 亮起只能作为相应功能的观察，不能据此把全部连线判定正确。

## 0.14：读端点候选、确定性回路判断、照片叠加

三条新路径都建立在上面的孔格标定之上，没有标定就不可用：

1. **Codex 读端点候选（`board_reader.py`，`POST /api/breadboard/read`）。** 孔格通过后点「让 Codex 读端点候选」。服务端重新矫正这帧（不信任页面传来的孔格），把**编号图**（`breadboard.labeled_warp()`：每孔距 32 像素，上下两排列号、左右行字母，电源轨每第 5 孔标名）和原照片一起发给 Codex，要求它用**孔名**（如 `e12`、`P1:7`）回答每根线端、每只脚插在哪里，只报告看得见金属进孔的位置。这是「Set-of-Mark」做法：模型选标号，不报坐标，也不沿线猜另一端。`reconcile_read()` 逐个核对孔名是否在标定孔格里、置信度是否达到 0.5、是否与已确认端点重复，并把部件改名为 `W1 / R1 / LED1(A/K) / BAT(+/-)`。结果只是**候选**（照片上橙色虚圈），你逐个对照原照片点「采用」后才进入连接表；`breadboard.evidence()` 仍只接受 `confirmed: true` 的端点。读板在文字通道上运行，不占用相机帧的分析通道。
2. **确定性回路判断（`circuit.py`）。** 每次校验连接表时，从同一组端点按 `W/R/LED/BAT/SW` 前缀识别部件，导线两端并入同一节点，其余部件作为节点之间的边，然后：找电源正负极之间的通路（优先包含最多元件的那条）；判断 `shorted_component`（两脚同节点）、`short_circuit`（只经导线就相通）、`led_without_resistor`、`reversed_polarity`（LED 以 A/K 命名且从正极先到 K）、`part_bypassed`（回路闭合但某元件不在其中）、`polarity_unknown`（LED 用 1/2 命名）、`conflict`（几何相通而实测不通）。回路不闭合时做**单步修复搜索**：把每个端点试着移到其他已用节点上最近的空孔，只接受移动后回路闭合且包含全部元件的方案，按「不会让导线悬空 → 列偏移最小 → 距离最小 → 导线端优先于元件脚」排序，给出「把 LED1.A 从 f10 移到 e10（上半区 a–e 行第 10 列，与 R1.2 相通）。移动前先断电」这种指令，并附核验办法（断电后量通断）；没有单步方案时列出悬空端点，明确说「不止一处改动或缺元件」。少于一个电源加一个元件时报 `incomplete` 并列出缺什么。这些结论只来自几何与录入，不来自看图；实测导通（`closed`）会把两个节点并入判断，实测不通与几何矛盾时报 `conflict`。
3. **照片叠加。** 冻结照片上画出已确认端点（绿圈）、候选（橙色虚圈）和建议的移动（橙色箭头指向目标孔）。
4. **交给 Codex 时**：`board_evidence.circuit` 随连接表一起进入完整分析上下文，提示词要求它直接转述 `findings` 里的 suggestion（含先断电），不另行推断改线；`incomplete` 时先让你录全缺的端点。

## ArUco

`.venv/bin/python scripts/make_board_markers.py --profile breadboards/terminal-63.json --out artifacts/board-markers.html` 生成可打印页面。按100%打印并核对实际2.54mm孔距。使用 `DICT_4X4_50` 的四个 ID 0–3。配置里的标记中心和边长以孔距为单位，必须对应实物上相对孔格的测量位置；不是在任意四角贴四张图就可用。标记须与板子刚性固定，不能让板子在纸上滑动后继续相信旧标定。

每帧检测全部标记，以16个角点拟合单应变换并检查重投影误差与孔纹理。缺标、重复ID或不可信拟合直接拒绝，不复用旧变换。现场尚未完成打印标记的实拍验收；当前自动测试覆盖合成标记及透视图。

## 板型与电源轨

`breadboards/*.json` 描述10行端子孔，`row_y` 是各行相对 a 行的孔距坐标。默认前五行和后五行分别同列相通，中缝不相通。不要在其他结构板上勾选确认。

默认配置只含端子孔。电源轨的真实孔位、孔间缺口和中途断开方式因板子不同而不同，必须明确配置，例如：

```json
{"name":"P1","x":[0,1,2,3,4,6,7,8,9,10],"y":-3,"groups":[0,0,0,0,0,1,1,1,1,1]}
```

放在 `rails` 数组中。这将产生 `P1:1` 等孔号；groups 相同的孔才在同一配置节点，`null` 表示该孔连通关系未知并单独隔离。这个例子不是对用户实物板的默认断言。轨道颜色不代表实际正负电压。

网表使用并查集合并已确认导线和配置孔组。测量图与几何图分开；不把电阻/LED短接。目标关系输出 `geometry_match`、`geometry_mismatch`、`measured_match`、`measured_mismatch`、`conflict`、`unknown`。缺少路径只能给 unknown，不能因没画出一根线就断言断路。

## API 与验证

- `GET /api/breadboard/profiles`：板型和依赖可用性。
- `POST /api/breadboard/rectify`：`{image: JPEG data URL, board: {profile, mode, layout_confirmed, anchors, endpoints, wires, measurements, expected}}`，返回校验指标、编号图、孔格、未确认颜色候选和连接表。
- `/api/analyze` 可附带同样的 `board`（含返回的 `frame_digest`）。服务端重新计算，不能直接上传一个“已通过”的结果绕过验证。
- `PYTHONPATH=tests:. .venv/bin/python -m unittest test_breadboard`。

## 实现参考与边界

本项目独立实现算法，未复制第三方源文件或模型权重。

- [sherlok-ohms grid/register.py](https://github.com/aliu3203/sherlok-ohms/blob/58119e675ba5a2fe18d69664eec46ca4ba5e1f56/grid/register.py)、[grid/layout.py](https://github.com/aliu3203/sherlok-ohms/blob/58119e675ba5a2fe18d69664eec46ca4ba5e1f56/grid/layout.py)：参考已知几何与标记配准、最近孔距离与歧义拒绝、按半区和电源轨分段计算节点的分层方式。AgentXR 使用六点手动校验或16个标记角点，并且不沿用 stale grid。
- [Breadboard-Classifier board.py](https://github.com/stonestephenson/Breadboard-Classifier/blob/758d5a37913c4b929926721380223c537491cbbe/breadboard/board.py)、[graph.py](https://github.com/stonestephenson/Breadboard-Classifier/blob/758d5a37913c4b929926721380223c537491cbbe/breadboard/graph.py)、[rectify.py](https://github.com/stonestephenson/Breadboard-Classifier/blob/758d5a37913c4b929926721380223c537491cbbe/breadboard/rectify.py)：参考几何与电气证据分离、拒绝不可信拟合和确定性检查。该项目也明确照片自动生成网表仍需训练数据；不能把其手工网表检查或电测能力等同于纯照片识别的准确率。未引入其角点模型、板型常数或 Arduino 驱动。
- [Circuit-Doctor analyze.py](https://github.com/ayushkumar-devlab/Circuit-Doctor/blob/main/analyze.py)：核对后它主要是单次VLM提示词和结果展示，没有可直接复用的几何追线器。
- [VLMs have Tunnel Vision](https://arxiv.org/html/2507.13361v1)：作为避免依赖模型沿长线追踪的研究依据。论文测试的模型和合成任务不等于当前模型在用户照片上的实测准确率；这里不据其数字作产品性能保证。

待有标注数据与实物验证后，才可替换人工端点确认为训练检测器；检测器输出仍须经过定位门槛和证据分层。无标记自动孔格拟合、自动识别LED极性、Arduino逐孔探测、自动执行改线均未实现。
