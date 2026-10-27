# 来源与迁移

本仓库提取自 NSLv2 的 `nsl_io`，来源提交：
`7177eb102cf0d0e87e240f1823a75adf9eddf04b`。

保留独立的字节/路径/内存适配、清单索引、发布队列与 CLI，不包含 NSLv2 的
dataset、infra cache、渲染/训练入口或模型配置。测试提取自原有 IO 合约测试，
移除了业务管线依赖，并增加独立包、大文件、asyncio 和内存输出测试。

新增功能包括：OSS 普通/分片上传自动切换，大文件复制流式传输，asyncio 便捷接口，
用于第三方可写文件/子进程的 tmpfs 目录，独立安装元数据及使用边界文档。

新代码推荐：

```python
from oss_io_tool import Path, io_session, read_bytes, write_bytes
from oss_io_tool.publisher import ArtifactPublisher
```

旧代码在仅安装本包的环境仍可 `import nsl_io`，两者共享同一模块、默认 IO、
ContextVar 和子模块；不会创建第二套上传队列。旧的 NSL 环境变量继续支持，新 OSS_IO 变量优先。

已同步 `nsl_io` 的大文件上传修复：达到 64 MiB 自动分片，默认 64 MiB 分片，
本地大文件上传使用 SDK 文件上传器的整体 CRC 校验；开启 `record_writes=True` 仍然流式上传，
摘要以分块读取计算。发布队列会即时记录后台失败并保留源文件。
`tests/test_nsl_large_upload_regressions.py` 移植原仓库的对应回归测试，覆盖超过 5 GiB 的稀疏文件、
同对象写入排序、条件写入、失败清理及后台异常传播。

NSLv2 工作树中的原实现暂时保留，以免现有训练部署因为未安装外部包而中断。
后续迁移时先在部署镜像/环境安装这个独立包，再统一替换 imports 或移除本地同名目录。
若同时把旧 NSLv2 根目录放在 `sys.path` 前面，本地 `nsl_io` 会遮蔽已安装的兼容入口；
因此迁移期间推荐使用 `oss_io_tool` 这个明确的新名字。

仓库默认不绑定远程，也没有擅自指定开源许可证；发布前由所有者决定仓库地址和授权方式。
