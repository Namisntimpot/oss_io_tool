# 使用边界与禁忌

常规调用只用统一 API。服务端上传限制由后端处理，调用方无需判断是否超过 5 GiB，
也无需导入 OSS SDK、手写分片或为大文件改写路径分支。

## 已封装的 OSS 上传限制

| 服务限制或常见错误 | 工具的处理 |
| --- | --- |
| PutObject 单请求不能超过 5 GiB | <= 64 MiB 简单上传，超过后自动使用 SDK Uploader 分片上传 |
| 一次分片上传最多 10,000 片，每片有大小限制 | 根据总大小自动计算分片大小，默认至少 8 MiB，可 seek 源最多 3 片并发，非 seek 源顺序分片以限制缓冲 |
| 分片失败留下不完整上传 | SDK 配置 `leave_parts_on_error=False`，在可捕获的失败时尝试 abort |
| 网络瞬时失败、数据损坏 | 使用 SDK 重试和默认请求/分片 CRC 校验；本地文件复制使用 SDK 文件上传器的整体 CRC 校验 |
| 写入一半就让读者看到完成状态 | 等 SDK 上传/合并完成才返回；后台写入通过 Future/flush 报错 |
| 上传大文件前先把本地文件完整读入内存 | `copy_file(local, oss)` 直接以文件流上传 |
| 用 HEAD 判断不存在再 PUT 导致竞争覆盖 | `write_object(..., overwrite=False)` 使用 OSS 的禁止覆盖条件；分片时也保留该条件 |
| 错把鉴权失败当成“不存在” | 仅 NoSuchKey 转为 FileNotFoundError；AccessDenied 等错误继续传播 |

以上上传策略覆盖 `write_bytes`、`write_text`、`Path.write_*`、远端 `open_file` 写入、
图像/模型保存适配器、`write_object` 和 OSS 目标的 `copy_file`。`copy_tree` 与发布器最终也走这些路径。

“单次 PutObject 最大 5 GiB”与“对象最大 5 GiB”是两回事；大对象由工具自动分片。
官方限制及上传协议见 [OSS Python v2 上传操作](https://www.alibabacloud.com/help/en/oss/developer-reference/upload-files-using-oss-sdk-for-python-v2/)
和 [PutObject](https://www.alibabacloud.com/help/en/oss/developer-reference/putobject)。
本工具为 SDK 的分片舍入预留一片，最多支持 `9,999 * 5 GiB`；超过工具上限会在上传前拒绝。

上传器的分片、重试与清理行为由 [OSS Uploader](https://www.alibabacloud.com/help/en/oss/developer-reference/large-file-upload-manager-using-oss-sdk-for-python-v2)
实现。内存/远端流使用 `upload_from`，本地文件复制使用 `upload_file`；均不写本地 checkpoint，
不提供跨进程崩溃后的断点续传。
进程被 SIGKILL/断电时无法执行 abort；bucket 管理员可配置清理未完成分片的生命周期规则。
网络、权限、额度或服务端失败仍会作为异常返回，工具不会静默忽略。

## 对象存储的语义边界

- **不要用 `pathlib.Path('oss://...')`。** 它会丢失 URI 双斜杠。使用 `oss_io_tool.Path`。
- **不要把云 URI 直接传给普通 `open`、`cv2.imread`、`np.fromfile` 或只认识本地路径的加载器。**
  使用工具适配器、`open_file`、BytesIO 或原生内存文件。
- **远端不支持追加/随机更新。** `open_file` 仅接受 `r/rb/w/wb`，拒绝 `a`、`r+` 等模式。
  `w/wb` 只在正常退出上下文后上传整个对象；上下文中异常时放弃发布。
  本地 `open_file` 是普通文件流，异常退出不会回滚已经写入的字节。
- **对象前缀不是目录。** 远端 `mkdir` 是空操作；远端 `is_dir`、`iterdir` 和 `glob` 明确报错，
  不以猜测方式查询前缀。
- **不要在训练/业务热路径扫描 bucket。** 管理阶段显式调用
  `IO.inventory(root, allow_remote=True)` 或 `oss-io inventory ROOT`，再把清单交给消费端。
  inventory 会自动分页，但它不是并发写入环境下的事务快照。
- **不要依赖 `copy_tree` 扫描远端源。** 它适合本地目录到任意目标；远端源需要显式清单循环 `copy_file`。
  CLI 的 `--recursive` 同样有这个限制。S3 管理型 inventory 尚未实现。
- **对象 ETag 不是通用内容哈希。** 分片/加密对象不能当作 MD5；需要内容摘要时自行记录 SHA-256 元数据。
  `read_pinned(oss_path, etag=...)` 用服务端 If-Match；本地 `read_pinned` 的参数实际是 SHA-256。
  不要把本地 `inventory` 的空 etag 或远端 ETag 当作 SHA-256 使用。
- **发布多文件不是事务。** 包清单最后发布只能说明此前的上传已返回成功；更新同一前缀时其他读者仍可能看到混合版本。
  对严格版本一致性，写入新的唯一前缀，完成后再发布指向它的清单/完成标记。

可以用 `IndexedFilesystem(root, inventory=...)` 对显式 `_io_files.json` 清单做 `smart_listdir` / `smart_glob`。
清单内容是相对文件名的 JSON 数组；不会调用云端列表 API。

## 同步、异步与完成信号

- 后台写入会在提交时复制成不可变 bytes，之后修改原 bytearray 不会改变上传内容。
  多个大对象同时排队会占用相应内存；`workers` 限制执行线程数，**不限制排队数据量**。
  使用批次 + `wait_futures` / `flush` 控制队列，或用有背压的 `ArtifactPublisher(max_pending=...)` 发布已落盘文件。
- 不要把“Future 已返回”当作“上传已完成”。完成标记必须在 `future.result()` / `flush()` 之后写。
  `flush` 会等待本实例所有已入队异步写入后抛出遇到的首个异常；正常退出会话也会等待并传播异常。
- 不要依赖隐式进程退出处理错误。`atexit` 仅是兜底，显式会话/flush 才能让上层可靠处理异常。
- 同一路径顺序只针对同一个 IO 实例、同一个字符串路径。`a/../x` 与 `x`、不同软链接或多个进程不构成同一队列。
  读取、删除和跨实例写入不会自动等待对方的后台任务。
- `open_file` 的本地写入是直接流式写入。若需要原子替换，用 `write_bytes` 或 `copy_file`。
  它们先写同目录临时文件再 `os.replace`，失败时保留已发布文件；不隐含 fsync 或跨文件事务。
- asyncio 使用 `async_read_*` / `async_write_*` / `async_flush`；不要在事件循环里调用阻塞 `Future.result()`。
  不支持 `async with open_file(...)`。协程取消不会撤销已提交的服务端写入。
- 退出会话前完成自建线程任务。`submit_io` 传播 ContextVar；会话不会自动管理你创建的任意线程池。
- `ArtifactPublisher.submit` 默认成功后删除源文件；失败保留源文件。始终调用 `drain()` 观察失败。
  正在发布的源文件应保持不可变。

## 内存、文件对象与原生库

- `read_bytes`、远端 `open_file` 和多数编解码适配器会把**整个对象**放入内存。
  自动分片解决上传协议限制，不让这些接口自动变成无限内存。
  本地↔OSS、OSS↔OSS 大文件复制优先用 `copy_file`；其正常路径按块传输。
  开启读缓存、读预算或写入记录/测试 sink 时，复制可能回到完整 bytes 路径，以保留这些功能的语义。
  S3 目标复制目前也走完整 bytes 路径。
- 需要读取大对象中的一段可使用 `IO.read_range(path, start, length)`；长度必须为正，结果会检查长度。
  `cache_reads=True` 缓存会话内的远端内容，会用 HEAD/ETag 检查版本；不写磁盘缓存，没有自动 LRU 淘汰。
  `max_remote_read_bytes` 限制累计下载预算，**不是**进程峰值内存限制。
- `BytesIO` 有 `read/seek`，但没有可用的本地文件描述符。要求 `fileno()` / 路径 / mmap 的接口需要 memfd/tmpfs。
  某些格式依赖文件后缀，在 BytesIO 保存时显式传格式。
- 原生库可能延迟打开文件。让作用域覆盖所有实际读取；例如 `np.load(.npz)` 返回的懒对象要在 stream/memfd 有效时读取并关闭。
  既有 mmap 映射可以比 memfd 路径存活更久，但库后续如果重新打开原路径仍会失败。
- `memory_file` 的实际名称是 `/proc/self/fd/N`。不能依赖后缀，不能传给外部进程。
  原生库若通过“同目录临时文件 + rename”保存，也应使用 `memory_directory`。
- `native_directory` 是输入清单适配器，文件通过符号链接指向本进程 memfd，不适合可写工作目录或子进程。
  需要真实文件、输出、重命名、扩展名或多进程共享路径，使用 `memory_directory`。
- `/dev/shm` 必须实际挂载 tmpfs，有独立容量限制；Docker 的共享内存配额常比系统 RAM 小，部署时配置足够的 `--shm-size`。
  memfd 和 tmpfs 都占用内存资源；受系统内存/cgroup 约束，tmpfs 也可能被系统换出。它们不是无限容量或不可交换内存。
  内存适配失败时工具不会偷偷改用磁盘。必须允许磁盘的调用才显式选择 `local_file` / `memory=False`。

## 配置与兼容

| 配置项 | 含义 |
| --- | --- |
| `OSS_IO_RETRY_MAX_ATTEMPTS` | SDK 最大尝试次数，正整数；默认使用 SDK 配置 |
| `OSS_IO_MEMORY_CACHE=1` | 默认 IO 开启会话内远端读缓存 |
| `OSS_IO_READ_BUDGET_BYTES` | 默认 IO 累计远端读取预算 |

仍兼容 `NSL_OSS_RETRY_MAX_ATTEMPTS`、`NSL_IO_MEMORY_CACHE`、`NSL_IO_READ_BUDGET_BYTES`；
新变量优先。默认 IO 在模块导入时读取缓存/预算配置，推荐在调用前设置环境，或通过 `io_session` 显式传参。
配置仅保存在运行环境，不要将凭据写进代码、示例或仓库。
