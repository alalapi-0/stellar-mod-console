# src/catalog

规划职责：归档/散装输入、hash、版本、变体、运行组件和选项清单；只读发现优先，不在UI线程扫描大包。

R01 提供只读调查工具，需 Python 3.10+；RAR/7Z 解码使用系统 libarchive。

```sh
python3 -m src.catalog.inventory --config .local/paths.json --output .local/r01
python3 -m unittest discover -s tests -p 'test_inventory.py' -v
```

先按 config/paths.example.json 设置本机路径。工具目前读入既有调查清单（state.json、remaining-packages.json、tool-packages.json），用于对照旧剑星输入；这不是通用玩家导入接口，后者在 R09 实现。所有原始路径、归档索引和存档保护哈希仅写入被忽略的 .local/。程序不解包、不启动下载的 EXE、不部署、不修改 MOD 或存档。

所有输入计算完整 SHA-256；剑星候选读取每个归档条目并校验解码。已有其他游戏归属的包保留哈希与排除原因，省去深度解码。完整下载、损坏/不支持解码、未完成下载、危险归档路径、复本和未知类型分别记录；未知不是兼容。每包解码上限为 64 GiB，超过时保留 DECODE_FAILED 原因，不能当损坏或通过。

中断后用同一命令追加 `--resume`：先重新计算已完成输入的哈希，字节未变才复用归档索引，变化则重新解码；失败记录在字节未变时保留。增量记录不是进度状态，当前事实只记 PROJECT_STATE.json。

R02 从已固定快照建立内容别名、组件/三件套索引与前置发现图：

```sh
python3 -m src.catalog.dependencies --inventory .local/r01/inventory.json --facts data/author_requirements.json --output .local/r02/catalog.json
```

相同内容保留权威引用与全部原路径；没有删除或回收操作。文件名中的 MOD ID/版本只作线索。作者说明、实际文件布局、未知的逐版本前置、许可和运行验收分别保存；图引用完整不表示前置已经齐全。data/author_requirements.json 是带日期的作者来源事实，不是第二份项目进度，也不是允许启用 MOD 的规则引擎。
