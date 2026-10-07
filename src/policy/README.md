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

Lua 调查使用词法分析，不运行脚本；动态属性、包装函数、条件分支、配置优先级与间接注册保留未知。数字键码仅对常见字母/数字/F 键依照 [Windows 虚拟键码](https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes)归一化，重叠仍是调查候选；[UE4SS 注册接口](https://docs.ue4ss.com/lua-api/global-functions/registerkeybind.html)的实际运行需按安装版本验证。

原配置的尾逗号只可移除后恢复调查元数据，明确保留严格格式失败；不修改源配置，不授权应用。重复 JSON 键拒绝解析，防止无声丢掉作者声明。顶层记录、声明的变体槽、实际可用选项和运行支持分别统计。

资源依赖采用 Unreal 虚拟路径匹配：不同作者项目的 `Project/Content` 映射到 `/Game`，Engine 和插件保持独立命名空间。原始目录路径仍用于追溯。R02-b 修正了此前仅按物理路径查找造成的 222 条未匹配引用；余下 34 条还没有安装资源证据，不据此推断玩家是否拥有 DLC。路径存在仍不证明身体、骨骼或物理配套。
