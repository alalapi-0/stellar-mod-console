# R00 环境与输入调查

本页供 R01 执行者确定发现范围；本机完整路径、原始清单和证据引用保存在被忽略的 .local/，不进入公开仓库。调查日期 2026-10-07；版本变化后重新核实，不能把此页视为持续实时监测。

## 本轮实读

- 源码位置：Projects 下独立 stellar-mod-console，与其他项目同级；game 只作外部输入/运行目标。
- 主机：Ubuntu 26.04.1 LTS；RTX 5070 Ti，报告显存 16303 MiB，NVIDIA 驱动 595.91.07。
- Steam 游戏 app 3489700，当前 build 24463856。
- 当前 mods.txt：DekCNS、UltraCamera、KittensJiggleMod、EVEExperience 启用；JiggleUpdate、SBRewardsMod 停用。
- 最新历史游戏日志的 UE4SS：3.0.1 Beta #0 / d3d1004。旧 state.selection 中 Jiggle 字段已经落后，不可当成当前激活事实。
- 全局 Codex AGENTS.md 为 v9；本轮读取了必要的配置元数据，不复制全局配置或凭据。本项目仅新增项目约定，未改变全局权限、模型、MCP、认证或其他项目。
- 默认 gh 存在 http_unix_socket null 的 CLI 故障；本仓库以 ignored 的无 token 配置复用 OS keyring，当前 user 查询成功。不将配置故障当成认证失效。

## 新下载的范围

只读顶层统计：566 个文件；508 个 ZIP/RAR/7Z/EXE 完整候选，42 个 crdownload 未完成项，其余 16 个文件不能自动排除，R01 检查是否为相关散装脚本/配置。

508 候选中，336 与旧剑星清单原路径及尺寸匹配，39 与其他游戏历史包匹配，133 未匹配候选。**133 不是新增 MOD 数**：混有工具、运行库、版本与副本。路径/尺寸不是全量 hash 验证；只有抽查的一份 loader 副本已证实 hash 相同。未完成下载不可作为可安装输入，也不会由规划轮删除。

| 新工具或包 | 调查结果 | 下一处理 |
| --- | --- | --- |
| SB Control Center 2.2.0 / Nexus 1959 | Flutter Windows 桌面程序 | 查来源/许可、读写范围、Proton 与功能接口 |
| Simple UE MOD Manager 1.6.4 / 199 | 单 Windows EXE，无包内 README/许可 | 外部工具待核验，不能声称可嵌入游戏 |
| JiggleGUI 1.0 / 1627 | Windows PyInstaller 程序与设置材料 | 查配置格式和职责，不因有打包材料就视为开源 |
| HD-ATOOL 6.1 / 1662 | 默认、k2、k3 三包写同名 HD-ATOOL_P 三件套，内容不同 | 同组单选；要求 UE4SS 3.1.0-6，独立配置验收 |
| RandomSwitchCNSSuit 1.7.0 / 2603 | Lua 和 README，要求 Chrisr0 指定 3.1.0-6 | 自动换装写入权、F5 冲突和 CNS 接口待验证 |
| ESAP Beta 0.7 / 3782 | ESAP_P 三件套，无 README | 前置和与 SEAP 的关系未知，不能仅按名称判断 |
| SpeedMasterEve 1.0.9 / 2205 | 普通/chunk26 两变体，同目标而 utoc 不同 | 变体互斥，移动写入权与 EVEExperience 逐项分析 |
| FlyingEve 1.1.4 / 2269 | 普通/chunk23 两变体，同目标而 utoc 不同 | 变体互斥，飞行/动画/角色状态验收 |
| Perfect Engine Optimizer / 3137 | RAR 存在，效果未测 | 查内容、实际改动与帧时间/显存，不按名称推荐 |

键位已知：CNS N/Alt+N；UltraCamera Shift+F8 及若干组合；Kittens =/-、Shift+Alt+B/V/H；ATOOL Alt+' / Alt+O / Alt+L；RandomSwitch F5。具体绑定仍须逐版本解析和实际测试，统一菜单不预先占用这些键。

## 复用的历史证据，绝非本轮全面测试

上一轮记录 336 个独立完整剑星包、330 解码；780 个拥有的安装文件、7 个基础游戏文件检查通过；新增 CNS 配置 128 份、总配置 138 份、静态可选项 796。选项不是独立 MOD 数。

最后一轮游戏启动测试持续 143 秒，CNS、Van Dance、SEAP 载入，FText 签名修复有效；结论只是 PASS_STARTUP_INITIALIZATION。逐服装视觉、动作、长会话和所有工具效果尚未验收。本轮未启动游戏、重新全量 hash 检查、安装或升级。

已知问题：SBRewards 的 C++ ABI 不符合当前 loader，历史加载卡住后停用；Container ID 冲突曾需受控重打包；42 个共享资源差异覆盖已作旧批次审阅，但不能泛化到新包。16 个旧纹理包转换受限、DLC 缺失、其他工具和旧变体有保留停用项目；R01 以实际结果清单重建完整状态，保留历史排除与未支持分母。

CNS 2.2 的 DekCNS_* 是 Blueprint→Lua 自定义事件，尚非统一 RPC。现有 EVEExperience 5.11.4、Kittens 2.3.5 和 UltraCamera 已有过程内状态协作，统一控制台应保持各功能单写入者。CNS 物理修复在缺 Physics Asset 时可能让角色掉穿场景；当前该选项关闭，不能未经验证打开。

## 本轮证据限制

此调查足以建立计划，不等于新工具兼容结论。R01 要固定完整输入 hash 清单与运行版本；R04/R05 建立稳定配置；R06/R07 才证明新游戏内界面和适配操作可行。官方文档和作者示例仅是接口线索，实际能力按对应轮的本机证据确定。
