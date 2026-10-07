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
