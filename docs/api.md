# API reference

本页按功能列出当前实现的全部公开函数、类和方法。主模块函数从 `oss_io_tool` 导入；
子模块的导入位置会单独标注。`nsl_io` 是兼容别名，共享同一个默认 IO 与会话。
签名中的 `*` 后参数只能通过关键字传入；`**kwargs` 透传给对应第三方库。
`path` 通常接受字符串或 `os.PathLike`，云路径应使用工具的 `Path` 保留 URI。

## 1. 会话、资源管理与 IO 配置

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `IO(*, workers=4, oss_client=None, cache_reads=False, seed_content=None, record_writes=False, write_sink=None, max_remote_read_bytes=None)` | `IO` 实例；支持 `with`。实例调用不改变模块函数所用会话。 |

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `io_session(**kwargs)` | 上下文管理器，进入后返回 `IO`；让模块函数、Path、格式适配器使用它；退出时等待后台写入并清缓存。 |
| `current_io()` | 当前 `IO`，无活动会话时返回 `default_io`。 |
| `flush()` | `None`；等待当前 IO 所有已入队后台写入，收集结果后传播首个异常。 |

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `IO.flush()` | 同上，只等待该实例的任务。 |
| `IO.close()` | 等待后台任务，关闭线程池，清空 memory_cache、seed_content、written_objects；异常继续传播。 |


| IO 构造参数 | 含义与默认值 |
| --- | --- |
| `workers=4` | 正整数；后台写入线程数，不是待处理数据量上限。 |
| `oss_client=None` | 注入 OSS v2 SDK 客户端/兼容实现；默认懒加载并根据运行环境创建。 |
| `cache_reads=False` | 缓存远端完整内容和范围读取的结果；完整读取仍会 HEAD 检查版本。 |
| `seed_content=None` | 可选 `{normalized_etag: bytes}`，用于显式预置远端完整读取缓存；匹配 ETag/长度时复用，不是通用 SHA-256 索引。 |
| `record_writes=False` | 在 written_objects 记录成功写入的 size/MD5；本地文件复制仍分块计算摘要。 |
| `write_sink=None` | 可选测试/替代后端协议，见本页第 11 类；正常 OSS 使用无需设置。 |
| `max_remote_read_bytes=None` | 完整/范围读取所记录的累计远端下载预算；超限时在下载前拒绝，不是 RSS 内存上限。 |

`default_io` 是进程默认实例，导入模块时创建、退出时尝试 close。业务代码优先显式使用会话。
`IO.__enter__` 返回该实例，`IO.__exit__` 调用 close。不要在退出会话后继续提交任务。

## 2. 字节、文本、文件流与对象状态

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `read_bytes(path)` | `bytes`；同步读取完整文件/对象。 |
| `read_text(path, encoding='utf-8')` | `str`；读取后按 encoding 解码。 |
| `write_bytes(path, data, *, asynchronous=False)` | 同步返回 `None`；`asynchronous=True` 返回 `concurrent.futures.Future`，入队前快照为 bytes。 |
| `write_text(path, data, encoding='utf-8', *, asynchronous=False)` | 文本编码后写入；返回值同 write_bytes。 |
| `open_file(path, mode='rb', *, encoding='utf-8', **kwargs)` | 上下文管理器，产生二进制/文本流；本地直接打开，远端内存缓冲且可 seek，远端仅 r/rb/w/wb。 |

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `IO.read_bytes(path)` | 同名模块函数的实例版本。 |
| `IO.read_text(path, encoding='utf-8')` | 同名模块函数的实例版本。 |
| `IO.write_bytes(path, data, *, asynchronous=False)` | 同名模块函数的实例版本。 |
| `IO.write_text(path, data, encoding='utf-8', *, asynchronous=False)` | 同名模块函数的实例版本。 |
| `IO.stat(path)` | 本地 os.stat_result；OSS/S3 返回至少含 st_size、st_mtime_ns 的对象；远端另有 etag，OSS 另有 metadata。 |
| `IO.exists(path)` | `bool`；本地可检查文件/目录，远端检查精确对象；鉴权错误继续传播。 |
| `IO.remove(path, *, missing_ok=True)` | `None`；删除文件/精确对象，不递归删目录/前缀；OSS DeleteObject 的缺失对象行为不受 missing_ok=False 改变。 |


远端 `open_file` 写入仅在正常退出上下文后同步上传；异常退出放弃发布。
本地流的写入即时修改文件，不提供异常回滚或原子替换。`Path.open()` 默认文本 r，模块 open_file 默认二进制 rb。
`IO` 本身没有 open_file、copy_file 或 async_read_bytes 方法，这些是模块函数。

## 3. asyncio、线程池与批次等待

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `async_read_bytes(path)` | 协程，await 得到 bytes；用 asyncio.to_thread 执行同步读并传播当前上下文。 |
| `async_read_text(path, encoding='utf-8')` | 协程，await 得到解码后的 str。 |
| `async_write_bytes(path, data)` | 协程，await 到写入完成并得到 None；使用当前 IO 的后台队列，取消后队列仍跟踪已提交写入。 |
| `async_write_text(path, data, encoding='utf-8')` | 协程，文本编码后按 async_write_bytes 写入。 |
| `async_flush()` | 协程，在线程中 flush，await 得到 None。 |
| `submit_io(pool, function, *args, **kwargs)` | Future；向用户提供的线程池提交函数，同时传播 ContextVar 中的 IO/缓存作用域。 |
| `wait_futures(futures)` | None；等待可迭代对象中的全部 Future 后，再传播首个失败；不返回任务结果。 |


这些函数不是原生异步 OSS SDK；普通 `Future` 应用 result() 或 asyncio.wrap_future 等待。
`io_session` / `open_file` 是同步上下文管理器，不能 `async with`。格式保存器的 asynchronous 只异步持久化，编解码仍在调用线程执行。

## 4. URI 路径与本地目录操作

`Path(*parts)` 返回实现 os.PathLike 的 URI 路径对象；内部 `_directory` 参数不是用户配置项。


| 表达式 / 属性 | 返回值 / 作用 |
| --- | --- |
| `str(path)` / `os.fspath(path)` | 保留 oss://、s3:// 的字符串。 |
| `path / child` / `base / path` | 拼接并返回新的 Path；绝对子路径会替换前缀。 |
| `Path.name` / `Path.stem` / `Path.suffix` | 文件名、去掉最后扩展名的文件名、最后扩展名。 |
| `Path.parent` / `Path.parents` | 父 Path / 祖先 Path 元组；云 bucket 根的 parent 仍为自身。 |
| `Path.parts` | 路径组成元组；云 URI 用字符串分割实现，不与 pathlib 的盘符/根分段完全等价。 |
| 比较、排序、hash | 按保存的路径值实现；不是文件实体同一性判断。 |

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `Path.joinpath(*parts)` | Path；拼接多个部分。 |
| `Path.with_name(name)` | Path；替换末级名称。 |
| `Path.with_suffix(suffix)` | Path；替换最后扩展名。 |
| `Path.relative_to(other)` | Path；要求位于指定路径之下，否则 ValueError。 |
| `Path.is_absolute()` | bool；云 URI 视为绝对路径。 |
| `Path.expanduser()` | Path；本地展开 ~，云 URI 保持不变。 |
| `Path.resolve(strict=False)` | Path；本地调用 pathlib.resolve，云 URI 保持不变。 |
| `Path.cwd()` | 当前本地工作目录的 Path。 |
| `Path.exists()` | bool；使用当前 IO 检查精确对象/本地路径。 |
| `Path.is_file()` | bool；远端以精确对象元数据查询判断。 |
| `Path.is_dir()` | bool；云路径抛 RemoteListingError。 |
| `Path.stat()` | 使用当前 IO 的 stat 结果。 |
| `Path.mkdir(mode=0o777, parents=False, exist_ok=False)` | None；本地创建目录；远端为空操作。 |
| `Path.iterdir()` | Path 迭代器；仅本地目录或支持 listing 的注入 sink。 |
| `Path.glob(pattern)` | Path 迭代器；本地 pathlib glob；云路径抛 RemoteListingError。 |
| `Path.rglob(pattern)` | Path 迭代器；使用 **/pattern；云路径抛 RemoteListingError。 |
| `Path.open(mode='r', **kwargs)` | 文件流上下文管理器；委托模块 open_file，默认文本模式。 |
| `Path.read_bytes()` | bytes；委托模块 read_bytes。 |
| `Path.read_text(encoding='utf-8', errors=None)` | str；支持 encoding、errors。 |
| `Path.write_bytes(data, *, asynchronous=False)` | None 或 Future；委托模块 write_bytes。 |
| `Path.write_text(data, encoding='utf-8', errors=None, *, asynchronous=False)` | None 或 Future；支持 encoding、errors、后台写入。 |
| `Path.unlink(missing_ok=False)` | None；删除精确路径；missing_ok 默认 False，OSS 缺失对象仍遵循 SDK 删除语义。 |

`Path` 不提供 rename、replace、rmdir、touch、chmod、readlink 等完整 pathlib 接口；不要假定云前缀具有目录语义。

## 5. 复制、范围读取与条件发布

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `copy_file(source, destination)` | None；同步复制；本地目标原子替换；本地→OSS、普通 OSS→本地/OSS 路径按流传输，OSS 目标自动处理大对象。 |
| `copy_tree(source, destination, *, exclude=())` | None；遍历本地源目录并合并复制；exclude 按文件的 name 排除，不是 glob；不删除目标多余文件；_view_done 文件延后发布。 |
| `read_pinned(path, etag=None)` | bytes；OSS 且 etag 非空时执行 If-Match；本地/S3 的 etag 参数按 SHA-256 内容摘要核对。 |
| `write_object(path, data, *, overwrite=True, metadata=None)` | None；同步发布。OSS 保留 metadata/禁止覆盖条件；本地 overwrite=False 原子 create-only；S3 不支持禁止覆盖或非空 metadata。 |

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `IO.read_range(path, start, length)` | bytes；start 为非负整数，length 为正整数，越过文件末尾拒绝；OSS 使用范围请求及可用 ETag 的 If-Match；S3 通过 seek/read。 |


OSS 分片策略为：小于 64 MiB 简单 PUT；达到阈值自动分片，默认至少 64 MiB，按大对象自动增大。
可 seek 源最多三片并发，非 seek 源顺序上传以限制预读；失败时尝试 abort，不写断点 checkpoint。
`copy_tree` 逐文件 read_bytes 后写入，**不保证大文件源流式读取**；大型目录可显式遍历本地源后逐个 copy_file。
`write_object` 的本地 metadata 不持久化。不要用 S3 ETag 作为 read_pinned 的 SHA-256。
带 ETag 的 OSS read_pinned 直接执行条件请求，绕过常规读取缓存、预算与计数；它不是预算封装入口。

## 6. 管理清单与 IndexedFilesystem

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `IO.inventory(path, *, allow_remote=False)` | 字典行生成器：path、size、etag、mtime_ns；仅 OSS 允许显式远端扫描并自动分页，S3 尚未实现。 |
| `IO.iterdir(path)` | Path 生成器；本地或注入 sink。 |
| `IO.glob(path, pattern)` | Path 生成器；本地或注入 sink。 |
| `IO.is_dir(path)` | bool；本地或注入 sink。 |

导入 `from oss_io_tool.index import IndexedFilesystem`。

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `IndexedFilesystem(root, inventory=None)` | 本地直接访问目录；远端读取 inventory 或 root/_io_files.json 的相对文件名 JSON 数组，不调用列表 API。 |

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `IndexedFilesystem.smart_open(path, mode='rb', *, encoding='utf-8', **kwargs)` | open_file 上下文管理器；传完整路径，不自动拼接 root。 |
| `IndexedFilesystem.smart_exists(path)` | bool；检查实际路径，不把清单内容当作实时存在性证明。 |
| `IndexedFilesystem.smart_listdir(path)` | list[str]；本地目录名/文件名，远端按清单返回直属子名。 |
| `IndexedFilesystem.smart_glob(pattern)` | list[str]；传完整路径模式；远端匹配显式清单，支持 **。 |

inventory 的 JSONL 元数据行与 _io_files.json 的相对文件名数组是两种格式，不能互换；例子见 README 的清单章节。

## 7. BytesIO、memfd、tmpfs 与原生库生命周期

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `memory_file(data=b'', *, name='asset')` | 上下文管理器，产生 /proc/self/fd/N 字符串；Linux memfd、可原生读写；name 仅调试名，无文件后缀。 |
| `memory_directory()` | 上下文管理器，产生 /dev/shm 上真实 tmpfs 目录；支持后缀、输出、重命名和子进程。 |
| `native_directory(root, files)` | 上下文管理器，按显式相对文件名列表读取 root 下内容；tmpfs 目录中的 symlink 指向本进程 memfd。 |
| `native_file_scope()` | 上下文管理器，统一拥有 native_path 产生的文件；不返回路径。 |
| `native_files(function)` | 同步函数装饰器，给一次函数调用包裹 native_file_scope；不能用来覆盖异步协程或生成器的延迟执行生命周期。 |
| `native_path(path, *, data=None, memory=True)` | str；活动 scope 内根据 path/data 创建 memfd 或磁盘 fallback；data=None 时通过统一 IO 读取；无 scope 时仅本地未注入 data 可原样返回。 |
| `local_file(data, *, name='asset')` | 上下文管理器，产生保留 name 后缀的临时磁盘文件；这是显式允许磁盘的 fallback。 |
| `publish_package(source, destination)` | None；本地源目录的文件逐个上传，最后写入相对文件名数组 _io_files.json。 |
| `native_package(root)` | 上下文管理器；本地 root 原样返回；远端读取 _io_files.json 后委托 native_directory。 |


`io.BytesIO` 是标准库类型，不是本包导出的类。只有显式支持 fsspec 的库才理解 memory://。
所有 native 路径只在相应上下文内有效；懒读应在退出前完成。native_directory 适合本进程输入读取，
需要修改文件或交给子进程时使用 memory_directory 真实文件；memfd/tmpfs 不会自动降级写磁盘。

## 8. 图像、数组、模型与几何格式适配器

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `load_image(path)` | Pillow Image；完成 image.load 后返回，不依赖后续输入流读取。 |
| `save_image(image, path, *, asynchronous=False, **kwargs)` | None 或 Future；按小写后缀选择 PNG/JPEG，kwargs 传 Image.save；其他格式请自行编码或使用 imageio。 |
| `imageio_read(path, *args, **kwargs)` | imageio.v2.imread 的结果；路径参数转 BytesIO，也可直接传文件对象；插件/格式依赖 imageio 安装情况。 |
| `imageio_write(path, array, **kwargs)` | None；编码为 bytes 后写入；默认 format 为路径后缀。 |
| `cv2_read(path, flags=None)` | ndarray 或 None；默认 IMREAD_COLOR，通常按 BGR 解码；flags 透传 imdecode。 |
| `cv2_write(path, array, params=None)` | bool；imencode 成功才同步写入，异常可继续传播；params 为 OpenCV 编码参数列表。 |
| `numpy_load(path, *args, **kwargs)` | np.load 返回值；路径无 mmap 时转 BytesIO；路径带 mmap_mode 时经 Linux memfd，包括本地路径；文件对象直接透传。 |
| `read_array(stream, dtype, count=-1)` | 拥有独立数据副本的 ndarray；从当前位置读取 count 项或剩余全部字节；不自动 reshape。 |
| `torch_load(path, *args, **kwargs)` | torch.load 返回值；文件对象透传，普通路径经 open_file，远端 mmap 用 memfd，本地 mmap 直接传原路径。 |
| `torch_save(value, path, *, asynchronous=False, **kwargs)` | 路径目标返回 None 或 Future；先在调用线程序列化再写入；文件对象目标直接 torch.save，asynchronous 不创建后台持久化任务。 |
| `safetensors_load(path)` | dict[str, torch.Tensor]；使用 safetensors.torch.load_file，经 memfd 加载，本地/远端均需 Linux；没有本包 safetensors_save 函数。 |
| `load_trimesh(path, **kwargs)` | trimesh 对象/Scene；按后缀识别格式，相对外部 buffer/texture 经同一 IO resolver；需枚举 keys 的格式/插件仍受远端列表限制。 |
| `load_bitmap(path)` | Mitsuba Bitmap；通过 MemoryStream 读取，调用方先设置 variant。 |
| `write_bitmap(bitmap, path, *, asynchronous=False)` | None 或 Future；Mitsuba MemoryStream 编码 PNG/JPEG/OpenEXR；调用方负责线性/显示空间转换。 |


安装选项：图像 images，数组 arrays，PyTorch/safetensors torch，mesh mesh，Mitsuba mitsuba。
NPZ 等延迟对象、mmap 与原生文件生命周期的区别见 README。
本包未提供 save_numpy、read_json、write_yaml、async_copy_file 等函数；请用公开字节/流接口组合标准库/格式库，或 submit_io 执行阻塞操作。

## 9. 有背压的 ArtifactPublisher

导入 `from oss_io_tool.publisher import ArtifactPublisher`。

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `ArtifactPublisher(max_pending=2)` | 后台发布队列；max_pending >= 1，有背压、单发布线程；它不是上下文管理器。 |

| 接口与签名 | 返回值 / 作用 |
| --- | --- |
| `ArtifactPublisher.submit(staged_path, destination_path, on_published=None, remove_source=True)` | None；入队文件发布；满队列会阻塞等待。回调成功后按 remove_source 删除源；发布失败即时日志且保留源。 |
| `ArtifactPublisher.sync_tree_coalesced(source_dir, destination_dir)` | bool；同一源目录已有正在处理的任务时返回 False，否则入队返回 True；按文件 size/mtime 跳过本实例已发布的未改变文件；不删除目标额外文件。 |
| `ArtifactPublisher.drain()` | None；等待全部发布任务，关闭发布器，再传播首个异常；重复调用可返回，关闭后不可再提交。 |


始终在 finally 中 drain，并让暂存文件活到 drain 完成。源文件在上传期间必须不可变。
队列通过 submit_io 继承当前会话；storage.flush() 不替代 publisher.drain()。
回调在后台发布线程运行，其异常也会导致任务失败；此时目标可能已上传成功而源仍保留。
日志树同步保留源文件、跳过名称包含 .tmp.upload. 的文件；它不是双向同步或目标镜像删除工具。

## 10. 命令行、异常与返回值约定


| 入口 | 行为 |
| --- | --- |
| `oss-io copy SOURCE DESTINATION` | 同步 copy_file。 |
| `python -m oss_io_tool copy SOURCE DESTINATION --recursive` | copy_tree；仅本地源可递归发现。 |
| `oss-io inventory ROOT` | 行输出 inventory JSONL；显式允许 OSS 行政扫描；S3 不支持。 |
| `python -m nsl_io ...` | 兼容上述模块 CLI。 |
| `RemoteListingError` | 云目录探测、隐式列表、未授权的远端 inventory 等不受支持操作。 |
| `FileNotFoundError` | 本地不存在、OSS NoSuchKey 等；S3 的异常类型也取决于 megfile。 |
| `FileExistsError` | write_object(overwrite=False) 命中已有本地/OSS 对象。 |
| `ValueError` | 不支持的远端 open 模式、非法范围/清单路径、配置错误、大小上限等。 |
| `RuntimeError` | 关闭 IO 后写入、读预算耗尽、缺少 Linux 内存设施、无 native scope 等。 |
| SDK / 编解码 / OSError 异常 | 网络、鉴权、格式和存储错误继续传播；后台错误在 result/flush/drain 等待点可观察。 |

除明确返回 Future、bool、对象/数据的函数外，写入/复制/等待操作通常返回 None。
Future 完成不等于跨文件事务或本地 fsync 持久性。__version__ 提供包版本字符串。

## 11. 观测指标、注入 sink 与环境变量


| IO 属性 | 含义 |
| --- | --- |
| `bytes_read` | 被该实例记录的逻辑读取字节，含缓存命中。 |
| `remote_bytes_read` | 被该实例记录/预留的累计远端下载字节；失败请求也可能已计入预算。 |
| `bytes_written` | 被该实例记录的成功写入字节。 |
| `cache_hits` | 完整读取或范围缓存命中次数。 |
| `memory_cache` | 会话内缓存字典；close 清空，没有自动 LRU。 |
| `written_objects` | record_writes=True 时 `{path: {size, md5}}`；同路径保留最新记录，close 清空；不是实时目录清单。 |

这些是特定代码路径的观测指标，不是全面计费/流量审计；本地 open_file 流、带 ETag 的 OSS read_pinned、
本地 create-only write_object 等路径不会完整计入。需要记录摘要时在会话退出前复制 written_objects。

注入 `write_sink` 是 duck-typed 协议，按所用功能实现：

| 方法 | 用途 |
| --- | --- |
| `matches(path) -> bool` | 是否接管该精确路径。 |
| `read(path) -> bytes` / `write(path, data)` | 完整字节读取/写入。 |
| `exists(path) -> bool` | 存在性。 |
| `info(path) -> dict` | 至少 size；可带 md5、directory，支持 stat/is_dir。 |
| `listing(path, recursive=False)` | 产生 `(full_path, is_directory)`，支持 iterdir/glob。 |

sink 不是 fsspec 插件系统，也不自动接管 remove、带 ETag 的 read_pinned 或 write_object 的 OSS 专用请求。
生产环境处理本地/OSS/S3 不需要自定义 sink。

| 环境变量 | 用途 |
| --- | --- |
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` | OSS AK/SK；优先于 ALIBABA_CLOUD 同名字段和配置文件。 |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | OSS AK/SK 的替代环境字段。 |
| `OSS_SESSION_TOKEN` | OSS 临时凭据 token；没有自动假定使用其他 token 环境名。 |
| `OSS_REGION` / `OSS_ENDPOINT` | region 必须有配置；endpoint 可选；环境优先。 |
| `OSS_CONFIG_FILE` | OSS 配置文件位置，默认 ~/.ossutilconfig。 |
| `OSS_IO_RETRY_MAX_ATTEMPTS` | SDK 最大尝试次数，正整数；兼容 NSL_OSS_RETRY_MAX_ATTEMPTS。 |
| `OSS_IO_MEMORY_CACHE=1` | default_io 开启缓存；兼容 NSL_IO_MEMORY_CACHE。 |
| `OSS_IO_READ_BUDGET_BYTES` | default_io 远端读取预算；兼容 NSL_IO_READ_BUDGET_BYTES。 |

新 OSS_IO 变量优先于旧 NSL 变量。default_io 的缓存/预算在导入时读取；显式 IO/io_session 的参数独立配置。
S3 的 SDK/凭据与 endpoint 由 megfile 管理，本包的 OSS 变量不自动配置 S3。
