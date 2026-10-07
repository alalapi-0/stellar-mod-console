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

Lua 调查使用词法分析，不运行脚本；动态属性、包装函数、条件分支、配置优先级与间接注册保留未知。已知字面键码按下文限定的名称和 [Windows 虚拟键码](https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes)归一化，重叠仍是调查候选；[UE4SS 注册接口](https://docs.ue4ss.com/lua-api/global-functions/registerkeybind.html)的实际运行需按安装版本验证。

R04-l 保留原 `keybind_calls` 调查分母，同时增加 `keybind_semantics`：
直接的注册名称、查询名称和未知包装分别记录；限定成员、局部/重赋值名称及
`pcall` 间接调用保留未知。查询名称不会被当作键位注册；这个分类不证明真实
Lua 绑定或回调已运行。配置值读取函数也不能仅凭名称推断为注册函数。

两参数与三参数注册形式分别记录修饰键。只有完整的字面修饰键列表才可形成
组合候选；动态成员、重复项、部分列表、未结束参数和未知重载均保留未知。
常用 [Key 名称](https://docs.ue4ss.com/lua-api/table-definitions/key.html) 与 VK
数字按文档候选归一化，包括数字区、数字小键盘、Insert 和方向键；不执行
Lua 表来验证对应值。多个声明使用同一主键时，修饰键不同也不证明不会同时
触发，分支、调用环境、启用与分发行为仍须实测。

`file_accesses` 只记录 `io.open`、`os.remove` 和 `os.rename` 的字面调用形式，
模式依据 [Lua I/O 文档](https://www.lua.org/manual/5.4/manual.html#pdf-io.open)。
这不是完整写入清单或安装版本证明。文件句柄写入、自定义保存函数、外部工具、
工作目录、路径别名和条件执行仍可能改变真实目标；所有写入权保持 `UNASSIGNED`。
参数名称相同或文件名相同不能证明两个模块写入同一目标，也不授予覆盖权。

共享配置预览只纳入显式选择的整包 Lua 证据；容器/加载器组件不会自动选择
同包脚本。查询不占键，动态/间接调用、同主键候选和未确定写入目标会加入
相应待验证项；旧索引缺少这些字段时明确显示证据缺失。当前报告仍为只读，
不会保存或部署配置，`can_apply` 始终为 false，旧配置保持在预览结果中。

本机 R04-l 重建保留 619 个文档、306 个 Lua 来源身份和原 1 个延期项。
原包 237/已安装 82 个含 keybind 名称的直接调用仍保留；各有另 4 个间接调用。
原包有 188 个注册名称、5 个查询名称，已安装有 37 个注册名称、1 个查询名称；
其余为未知包装。登记 66/26 个文件访问声明，其中已安装有 11 个写入/变更或
未知模式声明，不能据此证明这些代码当前会执行。`enabled_in_current_mods_txt`
仅是清单声明，不等于 enabled 标记、加载器实际启用或进程状态。
当前完整索引和复核结果在忽略的 `.local/r04/keybindings/`；旧索引不覆盖。

R04-m 的 `configuration_evidence.json` 由现有接口构建器读取，保存自有的
路径、读写和调用关系说明，不保存第三方源码。每条说明绑定完整脚本 SHA-256、
调用行、函数名称和读取/写入分类；字节变化不沿用旧说明，调用分类矛盾则拒绝。
预期模块名不同或缺失时不使用该布局的目标分组键。所有真实路径和写入权仍为
`UNVERIFIED` / `UNASSIGNED`，这些规则不能授权保存、迁移、删除或部署。
更新 MOD 后需重新核对对应来源，不按文件名或版本号自动套用规则。

当前核对覆盖 15 份不同 IO 脚本、36 个来源文档的 92 个声明，并另核对
UltraCamera 的启动调用来源。JiggleUpdate 与 JiggleUpdate2ATOOL 声明的
相对布局指向同一 `config.txt`：前者写四到五项，后者以替换模式只写
`enabled` 与 `profile`。共享预览展示目标候选、各写入者和声明字段，
需要验证同文件映射与字段保留；Kittens 的同名配置属于另一模块，不能只按
`config.txt` 名称合并。预设 Lua 会被 `dofile` 调用，不能作为普通无执行数据。

DekCNS 和 RandomSwitch 的存档读取、DekCNS 的截图复制/删除调用关系有独立
隔离待验证项；本轮未打开它们指向的存档或截图。Kittens 源码含启动读配置后
保存的调用，UltraCamera 源码含启动迁移预设的调用，不能把“只启动模块”
等同于“配置不变”。UltraCamera 的声明顺序是默认值、工厂预设、用户覆盖；
工厂读取失败会提前返回，迁移可能写用户预设并追加旧文件标记。它的默认配置
是相对工作目录路径，`cfg.path` 和后缀目标仍需运行时验证。

`configuration_target_candidates` 和 `configuration_source_observations` 由同一
只读规划入口输出；仅选择容器/加载器组件不会隐式选择同包脚本。多写入者、
存档/原媒体访问和启动写入说明产生待验证项，不转换成已证实兼容或已执行效果。
原 619 个来源身份、306 个 Lua、1 个延期项与历史失败均保留；本轮证据位于
忽略的 `.local/r04/config-targets/`。

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
