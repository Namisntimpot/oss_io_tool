# API 速查

所有下述主接口均可从 `oss_io_tool` 导入；`nsl_io` 为同模块兼容别名。

| 接口 | 行为 |
| --- | --- |
| `IO(workers=4, oss_client=None, cache_reads=False, seed_content=None, record_writes=False, write_sink=None, max_remote_read_bytes=None)` | 独立 IO 实例；支持 with/close/flush；`seed_content`、`write_sink` 用于缓存预置或注入测试后端 |
| `io_session(**kwargs)` | 建立当前作用域的 IO，参数同 IO；模块函数/Path 自动使用它 |
| `current_io()` | 当前会话或默认 IO |
| `read_bytes(path)` / `read_text(path, encoding='utf-8')` | 同步读取 |
| `write_bytes(path, data, asynchronous=False)` | bytes 快照；同步返回 None，后台返回 Future；asynchronous 为 keyword-only |
| `write_text(path, data, encoding='utf-8', asynchronous=False)` | 文本编码后写入；asynchronous 为 keyword-only |
| `flush()` | 等待当前 IO 的异步写入并传播异常 |
| `async_read_bytes` / `async_read_text` / `async_write_bytes` / `async_write_text` / `async_flush` | asyncio 协程，参数对应同步模块函数；await 写入直到完成 |
| `open_file(path, mode='rb', encoding='utf-8', **kwargs)` | 上下文管理器：本地直接流，远端 seekable 内存流；远端仅 r/rb/w/wb |
| `copy_file(source, destination)` | 同步复制；OSS 自动处理大对象；本地目标原子替换 |
| `copy_tree(source, destination, exclude=())` | 本地目录递归合并复制；远端源需要显式清单 |
| `wait_futures(futures)` | 等待全部 Future，再传播首个异常 |
| `submit_io(pool, function, *args, **kwargs)` | 向线程池提交并传播当前 IO 会话 |
| `read_pinned(path, etag=None)` | OSS If-Match 条件读；本地参数为 SHA-256 |
| `write_object(path, data, overwrite=True, metadata=None)` | 同步写；OSS 自动分片且保留 metadata/禁止覆盖条件；这些参数为 keyword-only |

`IO` 还提供 `stat`、`exists`、`remove`、`read_range`、`inventory`、`is_dir`、`glob`、`iterdir`。
`read_range(path, start, length)` 支持本地/OSS/S3。
`inventory(path, allow_remote=False)` 是生成器；远端 OSS 需要显式 opt-in，S3 暂不支持。
只包含文件，不包含目录标记。

`Path` 提供 `/`、`joinpath`、`parent`、`parents`、`name`、`stem`、`suffix`、`parts`、
`with_name`、`with_suffix`、`relative_to`、`is_absolute`、`expanduser`、`resolve`、`cwd`、
`exists`、`is_file`、`is_dir`、`stat`、`mkdir`、`iterdir`、`glob`、`rglob`、`open`、
`read_bytes`、`read_text`、`write_bytes`、`write_text`、`unlink`。
它是 URI 保留的 Path 子集，不是 pathlib 的完整替代。远端列表操作会抛 `RemoteListingError`。

| 内存/第三方适配器 | 用途 |
| --- | --- |
| `memory_file(data=b'', name='asset')` | 上下文内提供 Linux memfd 路径，支持原生读写 |
| `memory_directory()` | 上下文内提供 `/dev/shm` 临时真目录，可写、保留后缀、支持子进程 |
| `native_directory(root, files)` | 显式相对文件清单 → tmpfs 目录与 memfd 链接 |
| `native_file_scope()` / `@native_files` | 批量拥有 native_path 创建的文件 |
| `native_path(path, data=None, memory=True)` | 在上述 scope 内提供原生路径；data 可注入已获取的内容 |
| `local_file(data, name='asset')` | 显式的临时磁盘文件 fallback |
| `publish_package(local_dir, destination)` / `native_package(root)` | 上传目录及 `_io_files.json` / 按清单原生加载 |
| `load_image(path)` / `save_image(image, path, asynchronous=False, **kwargs)` | Pillow；save 支持 PNG/JPEG |
| `imageio_read(path, *args, **kwargs)` / `imageio_write(path, array, **kwargs)` | imageio.v2 格式编解码 |
| `cv2_read(path, flags=None)` / `cv2_write(path, array, params=None)` | OpenCV 内存编解码；write 返回编码是否成功 |
| `numpy_load(path, *args, **kwargs)` | NumPy，包括 memfd mmap 路径 |
| `read_array(stream, dtype, count=-1)` | 对流提供类似 np.fromfile 的读取 |
| `torch_load(path, *args, **kwargs)` / `torch_save(value, path, asynchronous=False, **kwargs)` | PyTorch；远端 mmap 走 memfd |
| `safetensors_load(path)` | safetensors.torch 原生路径加载 |
| `load_trimesh(path, **kwargs)` | trimesh；关联 buffer/texture 也通过统一 IO 读取 |
| `load_bitmap(path)` / `write_bitmap(bitmap, path, asynchronous=False)` | Mitsuba MemoryStream；调用方选择 Mitsuba variant |

`name`、`data`（native_path 中）、`memory`、`asynchronous` 等以真实函数签名的 keyword-only 为准。
具体可通过 `help(function)` 查看。

`oss_io_tool.index.IndexedFilesystem(root, inventory=None)` 提供清单驱动的
`smart_open`、`smart_exists`、`smart_listdir`、`smart_glob`。

`oss_io_tool.publisher.ArtifactPublisher(max_pending=2)` 提供有背压的
`submit(staged_path, destination_path, on_published=None, remove_source=True)`、
`sync_tree_coalesced(source_dir, destination_dir)` 和 `drain()`。
`drain()` 等待并关闭队列；回调仅在目标写入成功后执行。它不会自动 flush 另一个 IO 实例。
