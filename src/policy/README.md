# src/policy

规划职责：所有入口共享的前置、互斥、版本、功能写入权与应用方案；失败保持原配置。

R03 提供原始包的只读静态分析：

```sh
python3 -m src.policy.static --catalog .local/r02/catalog.json --inventory .local/r01/inventory.json --output .local/r03/conflicts.json
```

从原始归档在内存读取 UTOC 标识，不解包游戏资源或修改源文件。已知 Keyhole、ATOOL、Speed/Flying 变体与 UE4SS 加载器有单选约束；诊断函数只证明特定坏组合违反已知规则，不授权部署或声称合法组合运行兼容。缺项/未知仍阻止应用。

Container/Chunk 标识重叠是调查依据。相同内容、可排序覆盖、硬冲突和已转换 CNS 命名空间需要另外核实；不能把原包冲突图套到已重打包的游戏配置，也不能把原先 42 条覆盖判断无条件继承。完整运行控制器与所有入口接入仍在 R10。

资源元数据索引：

```sh
python3 -m src.policy.resources --catalog .local/r02/catalog.json --inventory .local/r01/inventory.json --output .local/r03/resources.json
```

读取本轮实际出现的 v2/v3 UTOC 目录、资源类型、大小及记录哈希，分别统计原包、当前安装、仅 MOD 的重叠。原游戏容器保留独立身份；路径/标识重叠和哈希相同均不自动生成兼容或加载顺序规则。未知格式、加密、无效引用和扫描期间改动会拒绝索引或明确记为延期。

记录哈希通常尚未对 UCAS 解压内容复算。R03-b 另对历史 42 组中的 90 个当前资源块通过现有外部 retoc 只读输出，在内存计算 SHA-256，并核对 11 套候选/当前三件套完全一致；这恢复了旧资源证据的适用文件范围，加载顺序与游戏效果仍需运行验收。第三方资产不写入仓库。

CNS 与 Lua 接口调查：

```sh
python3 -m src.policy.interfaces --catalog .local/r02/catalog.json --inventory .local/r01/inventory.json --resources .local/r03/resources.json --output .local/r03/interfaces.json
```

在内存读取原包小型脚本/配置，核对冻结哈希，记录 CNS 标识及网格/材质/物理/动画引用、模块提供者、Lua 注册调用、键位声明与可能的功能写入范围。CNS 当前 Lua 查询以配置路径与 UniqueFitID 为组合；跨文件同 ID 不直接判冲突。目录索引存在不证明类型或配套兼容，未找到也可能来自 PAK、DLC 或运行时资源。

`module_providers.providers` 只表示观察到该命名空间的脚本/配置；`entrypoint_providers` 与 `extension_providers` 分开记录直接 `Scripts/main.lua` 入口及其他提供者。编号物理预设不能仅因目录相同就当成完整模块，也不能据此排斥所有扩展。嵌套 `Scripts/helpers/main.lua` 不计作入口；消费者版本、实际写入权和 ABI 仍需确认。

`raw_script_target_overlaps` 比较模块相对路径、准确文件 SHA-256 和大小。归档外层路径及 Windows 分隔符可不同，ASCII 大小写按候选目标归一化；实际安装映射仍需验证。相同字节可保留多个来源，不同字节需要明确文件提供者或覆盖方案；两者都不授予部署权限，也不直接推断崩溃。配置、导入和运行入口后续必须共用同一规则引擎，当前只是只读证据。

Lua 调查使用词法分析，不运行脚本；动态属性、包装函数、条件分支、配置优先级与间接注册保留未知。数字键码仅对常见字母/数字/F 键依照 [Windows 虚拟键码](https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes)归一化，重叠仍是调查候选；[UE4SS 注册接口](https://docs.ue4ss.com/lua-api/global-functions/registerkeybind.html)的实际运行需按安装版本验证。

原配置的尾逗号只可移除后恢复调查元数据，明确保留严格格式失败；不修改源配置，不授权应用。重复 JSON 键拒绝解析，防止无声丢掉作者声明。顶层记录、声明的变体槽、实际可用选项和运行支持分别统计。

资源依赖采用 Unreal 虚拟路径匹配：不同作者项目的 `Project/Content` 映射到 `/Game`，Engine 和插件保持独立命名空间。原始目录路径仍用于追溯。R02-b 修正了此前仅按物理路径查找造成的 222 条未匹配引用；余下 34 条还没有安装资源证据，不据此推断玩家是否拥有 DLC。路径存在仍不证明身体、骨骼或物理配套。

R04-g 增加整套配置的只读预览：

```sh
python3 -B -m src.policy.profiles --catalog .local/r02/inc1/catalog.json --graph .local/r03/inc1/conflicts.json --interfaces .local/r03/inc1/interfaces.json --current .local/r04/configuration-preview/empty.json --requested .local/r04/configuration-preview/de68b165199e9298620a-requested.json --output .local/r04/configuration-preview/my-preview.json
python3 -B -m unittest discover -s tests -p 'test_profiles.py' -v
```

预览输入使用 `schema_version: 1`、`name`、`selections` 和 `providers` 四个字段。
选择清单可引用目录中的包 ID，或冲突图中的容器/加载器组件 ID。包 ID
表示整个原包，包括其中的全部容器与脚本；组件 ID 只选对应容器或加载器，
不会自动启用同包脚本。默认前置不自动选版本；显式 `providers` 映射只展开
候选依赖闭包，仍不能证明版本、身体、DLC 或 ABI 满足。未知前置保留为阻塞。

候选计划显示来源路径、哈希、别名、原页面证据和待核实的用途；原页面为空或
文件名仅为提示时保留未知，不伪造作者来源。已知互斥检查也覆盖展开的前置；
脚本目标与 Container/Chunk 重叠只表示需要核实提供者或覆盖规则，不推断崩溃。
整包和单组件的差别、前置提供者变化、增加/移除组件及退出后应用要求可回读。
预览保持传入的旧配置，无部署、配置保存或游戏/Steam/Cloud 效果；CLI 只在
本仓库忽略目录独占创建报告，已有报告不覆盖。

本轮覆盖全量 473 个相关来源，5 套原始加载器候选分别成档；18 个已知坏组合
拒绝，18 个替换预览去除旧组件。10 项预览回归与原 2 项静态规则回归通过。
每个候选的 `can_apply` 都是 `false`，不把静态预览当作 R10 的完整运行控制器。
这里的空基线和原包候选也不是当前安装的已转换配置，不能用于替换真实游戏。
固定当前安装命名空间、真实 Steam/存档隔离、冷启动与恢复仍须单独验证。

R04-h 核对当前安装配置，分别记录原包和安装后命名空间：

```sh
python3 -B -m src.policy.installed --inventory .local/r01/inc1/inventory.json --catalog .local/r02/inc1/catalog.json --resources .local/r03/inc1/resources.json --history /path/to/remaining-package-installation-results.json --game /path/to/StellarBlade --output .local/r04/installed-profile/my-snapshot.json
python3 -B -m unittest discover -s tests -p 'test_installed.py' -v
```

以上路径占位符须按私有配置替换。实际本机命令和路径保留在忽略目录的核对记录中；
其他玩家须提供自己的路径和输入，这还不是可移植安装器。
检查重新读取全部已登记安装文件的字节哈希，并核对 MOD 根目录的文件集合。
已登记的包内日志仍核对和保留，仅未登记的运行日志/转储不进入 MOD 目标集。
缺文件、内容变化、链接、大小写目标碰撞或未登记 MOD 文件会拒绝生成快照。

`mods.txt` 的顺序、启用/停用声明、Lua/C++ 入口和 `enabled.txt` 都分别记录；
实际加载与两种入口的优先关系仍为 `NOT_VERIFIED`，不能只改一条声明就声称停用。
加载器目录覆盖和游戏专用目录也单独记录，非默认目录未映射时保持未知。
此处依据[加载入口文档](https://github.com/UE4SS-RE/RE-UE4SS/blob/main/docs/guides/installing-a-c%2B%2B-mod.md)
和历史日志所报提交的[目录解析源码](https://github.com/UE4SS-RE/RE-UE4SS/blob/d3d10044d12566b869de56164bdaf5dbf36067b8/UE4SS/src/UE4SSProgram.cpp)
确定检查范围；没有由标签或源码页面认定本机二进制 ABI、运行版本或行为通过。

已核对 790 个文件：780 个 MOD/加载器项、7 个原文件、3 个存档备份。
后两类不进入来源选择或加载器替换计划。171 套安装后的容器分别成组；108 套
完整文件哈希与原包组件相同，其余保持安装命名空间，不能沿用原包互斥规则。
字节相同只能列出来源候选；空文件、历史转换目录关系和未知来源各有状态，
都不授予写入权或证明兼容。转换后的身体/资源/载入顺序仍须实测。

5 套加载器包的核心 DLL 对比发现：4 套的 `dwmapi.dll` 与 `UE4SS.dll` 都与
当前安装字节一致，另 1 套只有 `UE4SS.dll` 不同。因此不能按包名/发行标签
直接判断需要升级；整包里的脚本与设置仍可能不同，未随核心 DLL 自动选择。
所有计划仍为 `can_apply: false`，不会写游戏、复制资产、覆盖配置或启动进程。
10 项回归和 Root 的 15 项核对通过；本轮数据见 `docs/evidence/R04-h.json`。
