# 自有隔离工具与离线验证

本目录保存既有只读计划器、自有探针和进程收束实现。公开源码及合成测试不授权启动Flatpak、bwrap、Wine、Steam、游戏、GUI/GPU或更改主机策略。当前业务暂停，真实运行必须另有当前明确授权和实际依赖。

`bootstrap`、`client_plan`、`cloud_plan`、`isolation`保留原有输入、挂载、路径、环境、账号/Cloud与进程所有权检查。调用者输入自己的获授权路径，真实账号、存档和MOD原件不进入公开夹具。静态计划与拒绝用例不能证明实际客户端隔离。

`d3d12_probe`与`gpu_probe`保留原固定实验边界；离线测试可编译自有探针并静态检查其产物，但不执行PE、不启动显示或GPU。不得借可编译性把未授权的运行写成通过。

`flatpak_probe`绑定[固定R04-n源码契约](../../docs/contracts/R04-n.json)。该文件原字节用于semantic_candidate，不是当前运行许可；其旧Goal、遥测和Judge/Governor流程均为历史冻结接口。原源码的审批令牌、路径/摘要/实例/设备/网络拒绝和单次执行门保持原样，本轮不调用执行入口、不伪造审批文件，也不安装或启动退役角色。后续如需运行，应先核对当前所有者明确许可与原保护条件；不能靠删除guard或改文档取得权限。

合成测试见tests下各对应模块：自有临时fixture、模拟Unreal对象、注入的假子进程，以及确切拥有的进程清理。`test_flatpak_probe.FlatpakProbeTests.test_deployment_reader_binds_real_files_and_rejects_mismatch`读取真实主机部署文件，必须单列为实机检查；纯离线治理验证不运行它，其断言继续保留。原测试需要的`.local/r04`仅在自有验证候选中建立空父目录，不复制本机运行资料。

完整所有者历史部署、运行、故障、保护前像和审查记录保持本机原State及私有证据；公开工程文档不复刻现场库存。通过合成测试仅建立对应输入与代码版本的技术证据。
