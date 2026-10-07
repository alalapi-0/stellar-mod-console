# 开源复用与来源

读者为实现者和发行审查者；采用或升级任何依赖前更新具体版本、commit/hash、许可、通知和用途。本轮只读核对上游及本机材料，没有复制第三方代码。自有代码选择 MIT，不给外部内容重新授权。

| 组件/来源 | 许可证据 | 推荐路线 | 限制 |
| --- | --- | --- | --- |
| [RE-UE4SS](https://github.com/UE4SS-RE/RE-UE4SS) | [MIT](https://raw.githubusercontent.com/UE4SS-RE/RE-UE4SS/main/LICENSE) | 已验证 loader 外部依赖、Lua/Blueprint桥 | 锁定实际 ABI；主库 MIT 不替代所有依赖授权 |
| [ue4ss-ModMenu](https://github.com/mattdavida/ue4ss-ModMenu) | [MIT](https://github.com/mattdavida/ue4ss-ModMenu/blob/main/LICENSE) | Lua/UMG 游戏内模块面板候选，R06 实验 | 其他游戏示例不是剑星/当前 loader/Proton 证明 |
| [Dear ImGui](https://github.com/ocornut/imgui) | [MIT](https://raw.githubusercontent.com/ocornut/imgui/master/LICENSE.txt) | C++ UI/渲染路线备选 | 需实证游戏内覆盖层，中文、输入、样式和可访问性自己完成 |
| [retoc](https://github.com/trumank/retoc) | [MIT](https://raw.githubusercontent.com/trumank/retoc/master/LICENSE) | 固定版本外部 CLI，IoStore 索引/校验/受控转换 | 解析与重打包不等于兼容；源资源许可独立 |
| [repak](https://github.com/trumank/repak) | [MIT OR Apache-2.0](https://github.com/trumank/repak/blob/master/Cargo.toml) | PAK 离线索引/比较，按需采用 MIT | 压缩/Oodle 依赖独立核实，不随意打包 |
| [UAssetAPI](https://github.com/atenfyr/UAssetAPI) | [MIT](https://raw.githubusercontent.com/atenfyr/UAssetAPI/master/LICENSE) | 离线元数据/配置解析 | 游戏映射与资产不因工具开源获授权 |
| [UAssetGUI](https://github.com/atenfyr/UAssetGUI) | [MIT](https://raw.githubusercontent.com/atenfyr/UAssetGUI/master/LICENSE)、[NOTICE](https://github.com/atenfyr/UAssetGUI/blob/master/NOTICE.md) | 可选诊断工具 | 本机旧版本与上游主线构建依赖分开，不为 UI 直接塞入桌面工具 |
| [Vortex](https://github.com/Nexus-Mods/Vortex) | [GPLv3](https://raw.githubusercontent.com/Nexus-Mods/Vortex/master/LICENSE.md) | 独立借鉴配置档、部署、冲突展示 | 不把复制代码直接标为 MIT；若采用源码须另审组合许可 |
| [UE Mod Hub](https://github.com/Dekita/ue-mod-hub) | [自定义限制许可](https://raw.githubusercontent.com/Dekita/ue-mod-hub/master/LICENSE) | 交互理念，或用户另装的外部入口 | 禁止商业使用和同类软件直接复用源码；不得复制源码/CSS/主题/图片 |
| CNS / EVEExperience / Kittens / SEAP / ESAP / Van Dance / ATOOL 等 | 目前 permission_unknown，分别查看准确作者页面 | 本机路径、公开/获许可接口、自写适配器 | 有源码或能下载不等于开源；不得默认复制/打包原代码资产 |
| Control Center / Simple UE Manager / JiggleGUI / Trainer | 逐工具待核实 | 先只读格式/能力映射，再合法独立适配 | 外部二进制运行不等于游戏内功能或可重新分发 |

本机 UE Mod Hub 0.10.69 的 package.json 指向上游；本机目录没有可核验 .git，不能声称已核实其 remote/commit。其 LICENSE 与上述限制一致。

## 可借鉴的优点

管理库的分类/搜索、配置档切换、冲突原因和前置提示、可读的变更方案、作者更新提醒，均可用自己的模型和代码独立实现。接口适配保持工具责任，功能页融合在统一 UI；不把工具原代码、窗口或主题搬入新产品。

### 本次补充整理的管理能力参考

2026-10-07 只读核对本机包及可访问作者页面；下列页面功能描述不是本机运行验收，也不是可直接移植的接口。此次新增 25 个归档主要是外观/CNS 内容、Jiggle Update 和物理预设，管理器参考来自已有资料库，并没有把每个预设算成新管理器。

| 参考及本机版本 | 可独立借鉴的能力 | 证据与边界 |
| --- | --- | --- |
| [SB Mod Manager #89](https://www.nexusmods.com/stellarblade/mods/89)，文件名 2026.8.7.1 | 保留为管理器调查对象，具体能力待核实 | 当前作者页有内容访问门禁；本机包名不能证明功能、接口或许可，不改变账号设置获取内容 |
| [Simple UE MOD Manger #199](https://www.nexusmods.com/stellarblade/mods/199)，1.6.4 | 导入、分类、启用/停用、预览和自定义名称 | 作者页已核对；桌面工具。上传、修改和资源复用需按作者权限核实 |
| [SB Control Center #1959](https://www.nexusmods.com/stellarblade/mods/1959)，2.2.0 | 按 Logic/Movie/Generic/CNS 分类，拖入归档并识别目标位置，前置引导 | 作者页描述需 7-Zip 并可处理 CNS/UE4SS 安装；本项目仍需版本闭包、退出事务和前像检查，不直接采用自动加载器升级。作者权限限制仍适用 |
| [UE Mod Hub #206](https://www.nexusmods.com/stellarblade/mods/206)，下载文件 0.10.10 | 资料库、搜索、来源入口、下载/安装状态及统一导航 | 作者页已核对；本机另有 0.10.69 目录，两者不能混作同一版本验收。上游自定义许可限制仍有效，独立实现交互，不复制源码/主题 |
| [Jiggle physics GUI #1627](https://www.nexusmods.com/stellarblade/mods/1627)，本机文件名 1.0 | 参数编辑、默认值恢复、参数与实际生效状态区分 | 作者页说明编辑 #1397 的 SpringBoneTweaks.lua，修改后仍需游戏内 F1 重载；不是通用 MOD 管理器，未证明支持 BetterJiggle/Kittens。作者权限限制仍适用 |

CNS 的服装列表、ATOOL 的功能/动画选择、RandomSwitchCNS 的允许集合与自动切换也属于游戏内能力参考。它们的版本、变体、依赖、快捷键和写入权单独记录；调试 GUI、桌面 GUI 和作者描述都不能作为游戏内面板验收。

新增 #1397 包内说明和 Lua 静态分析可对应到准确归档：包含 JiggleUpdate 的 main.lua、默认 SpringBoneTweaks.lua 和 F1 绑定，UE4SS 准确版本仍未知。新增 #1774 三个包各含 14 个编号预设，没有 main.lua；它们是扩展数据，准确消费者版本待核实。10–23 与 17–30 覆盖同一批 7 个目标且字节不同，10–23 与 Replacer 重叠 5 个目标；这要求整套选择或明确覆盖计划，不等于已证明会崩溃。详细原包/文件/hash 对照保存在本机增量清单，不公开第三方原始内容。

[Nexus 文件规范](https://help.nexusmods.com/article/28-file-submission-guidelines)说明文件许可由作者决定；“Nexus 下载”不能成为重新打包授权。用户私有本机按路径使用与公开发行是不同边界。每项复用记录 required_notice、redistribution、modification、license_version 和 source_version；许可未知不进入发行包。

## 官方接口证据与版本限制

- [UE4SS RegisterKeyBind](https://docs.ue4ss.com/dev/lua-api/global-functions/registerkeybind.html)：基础快捷键，不保证统一界面输入协调。
- [ExecuteInGameThread](https://docs.ue4ss.com/dev/lua-api/global-functions/executeingamethread.html)：游戏线程调用；当前文档不自动适用旧版本额外参数。
- [C++ GUI tabs](https://docs.ue4ss.com/dev/guides/creating-gui-tabs-with-c%2B%2B-mod.html)：描述调试 GUI 扩展，不足以证明玩家游戏内覆盖层。
- [C++ API](https://docs.ue4ss.com/dev/cpp-api.html)：上游版本和本机 ABI 不一致，需按实际版本源码/导出验证。
- [UE4SS 构建](https://github.com/UE4SS-RE/RE-UE4SS)：Windows/MSVC 和受 Epic 访问许可约束的内容不能假定在 Linux 本地无条件构建/分发。
- [官方剑星参考](https://store.steampowered.com/app/3489700/Stellar_Blade/)：风格参考入口，游戏图片/字体/UI资产不随本仓库发行。

## 发行前清单

采用时固定精确版本与 hash；记录项目自身与依赖的许可覆盖；保留第三方版权/NOTICE；只打包获准且必要的部分；不发布 MOD 资产、游戏资产、存档、用户名路径、账号或日志。release 候选再执行全包检查，不以 .gitignore 代替审阅。
