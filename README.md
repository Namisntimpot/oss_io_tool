# oss_io_tool

使用同一套 Python API 读写本地文件、阿里云 OSS 和 S3。存储层负责路径分派、上传和后台任务；
JSON、图像、数组、模型等格式通过文件对象或内存适配器处理。核心只依赖标准库，其他功能按需安装。

从 NSLv2 的 `nsl_io` 提取，可独立安装，不依赖训练/渲染工程或其 `PYTHONPATH`。
旧的 `import nsl_io` 是兼容别名，与 `oss_io_tool` 共享会话和上传队列。

## 导航与场景选择

| 需求 | 推荐入口 | 本文位置 |
| --- | --- | --- |
| 字节、文本、路径、元数据、删除 | `Path`、`read_*`、`write_*`、`IO.stat/remove` | [基础读写](#basic-io) |
| JSON、JSONL、CSV、YAML、pickle | 格式库 + `read_text/write_text/open_file` | [结构化文件](#structured-formats) |
| gzip、ZIP、TAR 等压缩/归档 | 格式库 + `open_file` / `BytesIO` | [压缩与归档](#archives) |
| 同步、后台写、并发读、asyncio | `io_session`、`submit_io`、`async_*` | [并发与会话](#concurrency) |
| 上传/下载大文件、复制目录 | `copy_file`，本地源目录用 `copy_tree` | [复制与发布](#copy-publish) |
| 持续发布 checkpoint / 日志树 | `ArtifactPublisher` | [有背压的发布队列](#artifact-publisher) |
| 精确范围、只创建、版本校验 | `IO.read_range`、`write_object`、`read_pinned` | [范围与条件 IO](#integrity) |
| 目录发现、冻结清单、离线匹配 | `IO.inventory`、`IndexedFilesystem` | [清单与索引](#inventories) |
| 复用远端输入、限制累计读取、记录摘要 | `IO` 的缓存/预算/观测参数 | [缓存与统计](#cache-observability) |
| PNG/JPEG、imageio、OpenCV | `load_image/save_image` 等 | [格式适配器](#format-adapters) |
| NPY/NPZ、裸二进制、PyTorch、safetensors、mesh、Mitsuba | 对应适配器 + 字节接口 | [格式适配器](#format-adapters) |
| 第三方库只接受 fileobj、本地文件名或目录 | `BytesIO`、memfd、tmpfs、native scope | [原生库与内存](#native-memory) |
| 命令行、可运行示例、完整签名 | CLI、`examples/`、分类索引 | [示例目录](#examples)、[API reference](#api-reference) |

“统一”指调用入口和字节内容一致；对象存储仍不具有本地目录、追加和随机修改的语义。

| 能力 | 本地 | OSS | S3 |
| --- | --- | --- | --- |
| 字节/文本、同步/后台写、asyncio | 支持 | 支持 | 支持，安装 s3 extra |
| `open_file` 的 r/rb/w/wb | 直接文件流 | 可 seek 的整对象内存流 | 可 seek 的整对象内存流 |
| 追加、r+、原地随机修改 | 普通本地 open 模式 | 不支持 | 不支持 |
| 精确存在性、文件大小、范围读、删除 | 支持 | 支持 | 支持 |
| `mkdir` | 创建目录 | 空操作 | 空操作 |
| `iterdir/glob/is_dir` | 支持 | 拒绝隐式云扫描 | 拒绝隐式云扫描 |
| 显式行政 inventory | 支持 | 需 allow_remote=True，自动分页 | 尚未实现 |
| create-only `write_object(overwrite=False)` | 支持 | 支持，包括分片上传 | 不支持 |
| `write_object(metadata=...)` | metadata 不持久化 | 支持 | 不支持非空 metadata |
| pinned read 的期望值 | SHA-256 内容摘要 | ETag / If-Match | SHA-256 内容摘要，非服务端条件读取 |

## 安装与连接配置

```bash
git clone https://github.com/Namisntimpot/oss_io_tool.git
cd oss_io_tool
python -m pip install -e '.[oss]'
# 只用本地与标准格式：python -m pip install -e .
# 多个功能可组合：python -m pip install -e '.[oss,images,arrays]'
```

| extra | 功能 | 主要依赖 |
| --- | --- | --- |
| `oss` | 阿里云 OSS | alibabacloud-oss-v2 |
| `s3` | S3 | megfile |
| `arrays` | NumPy | numpy |
| `images` | Pillow、imageio、OpenCV | numpy、Pillow、imageio、opencv-python-headless |
| `torch` | PyTorch、safetensors | torch、safetensors |
| `mesh` | 几何与场景 | trimesh、numpy、Pillow |
| `mitsuba` | Mitsuba Bitmap | mitsuba；数值示例额外需要 arrays |
| `test` | 离线回归测试 | pytest、OSS SDK、图像/mesh 依赖；不含 PyTorch/Mitsuba |

Python >= 3.10。BytesIO 和普通文件读写跨平台；memfd、tmpfs 目录及使用它们的适配器需要 Linux。
YAML 等其他格式库单独安装，例如 `python -m pip install PyYAML`；它们不属于本包核心依赖。

OSS 凭据只需在运行环境配置一次：

```bash
export OSS_ACCESS_KEY_ID='<access-key-id>'
export OSS_ACCESS_KEY_SECRET='<access-key-secret>'
export OSS_REGION='cn-hangzhou'
# 临时凭据额外设置 OSS_SESSION_TOKEN。
# endpoint 可省略；需要指定时按网络/地域设置，internal 仅适合同地域阿里云内网。
export OSS_ENDPOINT='https://oss-cn-hangzhou.aliyuncs.com'
```

支持 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` 作为 AK/SK 替代字段。
也可设置 `OSS_CONFIG_FILE`（默认 `~/.ossutilconfig`），使用 `[Credentials]` 或 `[default]` 节中的
`accessKeyId`、`accessKeySecret`、`region`、`endpoint`、`stsToken`；环境变量优先。
已有 OSS v2 客户端可通过 `io_session(oss_client=client)` 注入。S3 的凭据、endpoint 由 megfile 配置，
不读取本包的 OSS endpoint 设置。凭据留在运行环境，不放入示例或仓库。

<a id="basic-io"></a>

## 1. 基础读写、路径、元数据与删除

下面的业务代码只需更换 `root`，即可从本地切换到 OSS 或 S3。

```python
from oss_io_tool import Path, io_session, open_file, read_bytes, read_text, write_bytes, write_text

root = Path('/tmp/io-demo')
# root = Path('oss://my-bucket/io-demo')
# root = Path('s3://my-bucket/io-demo')

with io_session() as storage:
    write_text(root / 'message.txt', '你好，IO', encoding='utf-8')
    assert read_text(root / 'message.txt') == '你好，IO'
    write_bytes(root / 'data.bin', b'HEADER:payload')
    assert read_bytes(root / 'data.bin') == b'HEADER:payload'
    with open_file(root / 'data.bin', 'rb') as stream:
        stream.seek(7)
        assert stream.read() == b'payload'
    with (root / 'report.txt').open('w') as stream:
        stream.write('line 1\nline 2\n')
    assert (root / 'report.txt').exists()
    assert (root / 'report.txt').is_file()
    stat = storage.stat(root / 'data.bin')
    print(stat.st_size, stat.st_mtime_ns)
    print(getattr(stat, 'etag', None), getattr(stat, 'metadata', {}))
    (root / 'report.txt').unlink(missing_ok=True)
    storage.remove(root / 'another-absent-file', missing_ok=True)
```

`read_bytes/read_text` 整文件读取；`open_file` 在本地直接打开文件，远端先放入内存，提供 seek/read/write 接口。
远端写入只在上下文**正常退出**后发布，异常退出不上传；本地写入是普通文件行为，不提供异常回滚。
如果本地也需要原子替换，用 `write_bytes/write_text/copy_file`，不要用直接写模式的 open_file。
原子替换不等于断电后的 fsync 持久性。

### 路径组合与本地目录

```python
from oss_io_tool import Path

path = Path('oss://my-bucket', 'dataset', 'sample.npy')
assert str(path.parent / path.name) == 'oss://my-bucket/dataset/sample.npy'
assert str(path.with_suffix('.npz')) == 'oss://my-bucket/dataset/sample.npz'
assert str(path.with_name('other.npy').relative_to(path.parent)) == 'other.npy'
assert path.is_absolute()

local = Path('/tmp/io-demo/local')
local.mkdir(parents=True, exist_ok=True)
(local / 'one.txt').write_text('1')
print([p.name for p in local.iterdir()])
print([str(p) for p in local.rglob('*.txt')])
print(Path.cwd(), Path('~/data').expanduser(), local.resolve())
```

不要用 `pathlib.Path` 拼接云 URI，它会改变 `oss://` 中的斜杠。
工具 Path 只实现这里列出的 pathlib 子集，不提供 rename/rmdir/chmod 等全部接口。
删除是精确文件/对象删除，不递归删除目录或云前缀。OSS 删除不存在的对象沿用 SDK 语义，
不能依赖 `missing_ok=False` 在远端产生 FileNotFoundError。

<a id="structured-formats"></a>

## 2. JSON、JSONL、CSV、YAML、pickle 等结构化文件

格式处理交给格式库，存储处理交给统一 IO；不需要另外判断本地或 OSS。

```python
import csv
import json
from oss_io_tool import Path, open_file, read_text, write_text

root = Path('/tmp/io-demo/formats')  # 可直接换为 oss:// 或 s3://
records = [{'id': 1, 'name': '样本'}]
write_text(root / 'config.json', json.dumps(records, ensure_ascii=False, indent=2))
assert json.loads(read_text(root / 'config.json')) == records

with open_file(root / 'records.jsonl', 'w') as stream:
    for record in records:
        stream.write(json.dumps(record, ensure_ascii=False) + '\n')
with open_file(root / 'records.jsonl', 'r') as stream:
    assert [json.loads(line) for line in stream] == records

with open_file(root / 'records.csv', 'w', newline='') as stream:
    writer = csv.DictWriter(stream, fieldnames=['id', 'name'])
    writer.writeheader()
    writer.writerows(records)
with open_file(root / 'records.csv', 'r', newline='') as stream:
    assert next(csv.DictReader(stream))['name'] == '样本'
```

远端 JSONL/日志不能按追加模式写入；每批写一个独立对象，或在内存收集后发布完整对象。
逐行消费远端 open_file 只是提供文件对象接口，底层仍会先下载完整对象。

```python
# YAML：先单独安装 PyYAML。
import yaml
from oss_io_tool import Path, read_text, write_text

path = Path('/tmp/io-demo/config.yaml')
write_text(path, yaml.safe_dump({'batch_size': 8}, allow_unicode=True))
assert yaml.safe_load(read_text(path)) == {'batch_size': 8}
```

pickle、MessagePack、Protobuf 等输出 bytes 的库，直接组合 `write_bytes/read_bytes`。
例如 `write_bytes(path, pickle.dumps(value))` 与 `pickle.loads(read_bytes(path))`；pickle 只用于可信输入。
没有内置 `read_json/write_yaml` 等名称，以上组合就是这些需求的完整写法。
完整可运行版本：[structured_formats.py](examples/structured_formats.py)。

<a id="archives"></a>

## 3. gzip、ZIP、TAR 与压缩归档

```python
import gzip
import json
import zipfile
from oss_io_tool import Path, open_file, read_bytes, write_bytes

root = Path('/tmp/io-demo/archives')
payload = json.dumps({'step': 10}).encode('utf-8')
write_bytes(root / 'state.json.gz', gzip.compress(payload))
assert json.loads(gzip.decompress(read_bytes(root / 'state.json.gz'))) == {'step': 10}

with open_file(root / 'state.zip', 'wb') as raw:
    with zipfile.ZipFile(raw, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('state.json', payload)
with open_file(root / 'state.zip', 'rb') as raw:
    with zipfile.ZipFile(raw) as archive:
        assert archive.read('state.json') == payload
```

TAR、gzip 的流包装方式与上述一致：`tarfile.open(fileobj=stream, mode='w:gz'/'r:gz')`、
`gzip.GzipFile(fileobj=stream, mode='wb'/'rb')`；见 [structured_formats.py](examples/structured_formats.py) 的完整 TAR/gzip 读写。
打开外层 IO 上下文，再打开内层格式上下文，让格式库先写完尾部索引/校验信息，再上传完整对象。
压缩 TAR 中的成员不能直接用未压缩文件偏移进行远端范围读取；已知偏移的裸 TAR 成员可使用 read_range。

<a id="concurrency"></a>

## 4. 同步、后台写入、并发读与 asyncio

### 会话和显式实例

模块函数、Path 和格式适配器使用当前 `io_session`。`with IO() as storage` 只管理该实例，
不会改变模块函数所用的会话：

```python
from oss_io_tool import IO, Path, io_session, write_text

with IO(workers=2) as storage:
    storage.write_text('/tmp/io-demo/explicit.txt', '使用该实例')
with io_session(workers=2) as storage:
    write_text('/tmp/io-demo/session.txt', '使用当前会话')
    Path('/tmp/io-demo/path.txt').write_text('也使用当前会话')
```

### 后台写入与完成标记

```python
from oss_io_tool import Path, io_session, read_bytes, wait_futures, write_bytes, write_text

root = Path('/tmp/io-demo/background')
with io_session(workers=4) as storage:
    write_text(root / 'status.txt', 'started')  # 同步，完成后返回 None
    payload = bytearray(b'snapshot')
    future = write_bytes(root / 'a.bin', payload, asynchronous=True)
    payload[:] = b'changed!'  # 上传内容在提交时已快照
    future.result()          # 等待单次写入；失败会抛异常
    futures = [write_text(root / f'{i}.txt', str(i), asynchronous=True) for i in range(4)]
    wait_futures(futures)     # 等待全部任务，再抛遇到的首个失败
    storage.flush()          # 等待本会话所有已入队后台写入
    write_text(root / '_DONE', '1')
    assert read_bytes(root / 'a.bin') == b'snapshot'
```

同一 IO 对同一**字符串路径**的写入按提交顺序执行，后来的同步写/复制也会等待之前的写入。
读取和删除不会自动等后台写入，读回或删除前先等待 Future/flush。
其他 IO 实例、进程或另一个等价路径字符串不共享顺序。
`workers` 限制执行线程数，不限制待处理数据总量；持续写大对象应分批等待，避免积压内存。
会话正常或异常退出均会等待自身后台写入并清理缓存，上传异常会传播；不要只依赖 atexit。

`asynchronous=True` 只异步执行保存器最后的持久化：save_image、torch_save、write_bitmap 的编解码仍在调用线程完成。
需要把编解码也移出主线程时，使用 `submit_io(pool, torch_save, value, destination)` 之类的线程任务。

### 并发读取与线程中的会话传播

```python
from concurrent.futures import ThreadPoolExecutor
from oss_io_tool import Path, current_io, io_session, read_text, submit_io, wait_futures

root = Path('/tmp/io-demo/background')  # 上例已创建这些文件
with io_session() as storage:
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert submit_io(pool, current_io).result() is storage
        jobs = [submit_io(pool, read_text, root / f'{i}.txt') for i in range(4)]
        wait_futures(jobs)
        assert [job.result() for job in jobs] == ['0', '1', '2', '3']
```

普通 `pool.submit` 不自动传播 ContextVar；用 submit_io 保持 IO/cache 作用域。
自建线程池必须在会话退出前结束任务。完整例子：[threaded_io.py](examples/threaded_io.py)。

### asyncio

```python
import asyncio
from oss_io_tool import Path, io_session, async_read_text, async_write_text, async_flush

async def main(root):
    root = Path(root)
    with io_session(workers=4):
        await asyncio.gather(*(async_write_text(root / f'{i}.txt', str(i)) for i in range(4)))
        assert await async_read_text(root / '0.txt') == '0'
        await async_flush()
        await async_write_text(root / '_DONE', '1')

asyncio.run(main('/tmp/io-demo/asyncio'))
```

`async_read_bytes/async_write_bytes` 用法相同，处理 bytes。
这些是把阻塞 IO 交给线程的协程；不是原生异步 OSS SDK，也不支持 `async with open_file`。
普通后台 Future 在 asyncio 中用 `await asyncio.wrap_future(future)`，不能直接 await 或阻塞调用 result。
取消写协程不等于撤销上传，已提交写入仍由会话跟踪；读协程取消后，读取线程也可能继续运行，
应管理好任务与会话的生命周期。见 [asyncio_io.py](examples/asyncio_io.py)。

没有专门 async 便捷函数的操作，可把整个阻塞调用放进线程，并显式 await：

```python
import asyncio
from oss_io_tool import copy_file, io_session

async def copy_without_blocking_event_loop(source, destination):
    with io_session():
        await asyncio.to_thread(copy_file, source, destination)
```

这样可复用 copy_file 的大文件流式策略；必须等这个任务完成后再退出会话，flush 不跟踪任意自建线程任务。

<a id="copy-publish"></a>

## 5. 单文件、目录、模型包和大文件复制

```python
from oss_io_tool import Path, copy_file, copy_tree, io_session

with io_session():
    copy_file('/path/to/checkpoint.pt', 'oss://my-bucket/checkpoint.pt')
    copy_file('oss://my-bucket/checkpoint.pt', '/path/to/download.pt')
    copy_file('oss://my-bucket/checkpoint.pt', 'oss://my-bucket/archive/checkpoint.pt')
    # 本地源目录递归合并到任意目标；exclude 按文件名匹配。
    copy_tree('/path/to/model', 'oss://my-bucket/model', exclude=('debug.log',))
```

本地→OSS、普通 OSS→本地/OSS 的 copy_file 按流传输，不先构造整个文件的 bytes。
OSS 目标自动选择简单上传或分片，调用方无需为单次 PutObject 的大小限制写分支；
本地大文件使用 SDK 文件上传器，保留整体 CRC 校验，即使开启 record_writes 也保持流式上传和分块摘要计算。
本地目标先写同目录临时文件，再原子替换；复制失败保留已发布文件。

copy_tree 遍历本地源，逐文件读取后写入，不保证其中的大文件源流式读取；
大型目录优先本地遍历 + 逐个 copy_file：

```python
from oss_io_tool import Path, copy_file

source = Path('/path/to/model')  # 本地目录
for file in source.rglob('*'):
    if file.is_file():
        copy_file(file, Path('oss://my-bucket/model') / file.relative_to(source))
```

远端源不要 rglob；使用显式清单循环 copy_file。S3 目标、启用远端读缓存/预算或某些记录路径时，
复制可能使用完整 bytes；区别详见[使用边界](docs/pitfalls.md)。目录复制是合并，不删除目标多余文件。

### 多文件模型包：文件写完，最后发布清单

```python
from oss_io_tool import publish_package, native_package

publish_package('/path/to/local-model', 'oss://my-bucket/model-v1')
with native_package('oss://my-bucket/model-v1') as local_directory:
    # model = library.load_from_directory(local_directory)
    # 在这里完成库可能进行的延迟文件读取。
    pass
```

publish_package 上传文件后写入 `_io_files.json`；native_package 按这个相对文件名数组准备原生目录，
本地 root 则原样返回。清单最后发布不构成多文件事务，严格版本一致时用新的唯一前缀，完成后再更新指针/标记。
完整可运行版本：[copy_and_publish.py](examples/copy_and_publish.py)。

<a id="artifact-publisher"></a>

## 6. checkpoint 与日志树的有背压发布

```python
from oss_io_tool import io_session, write_text
from oss_io_tool.publisher import ArtifactPublisher

with io_session():
    publisher = ArtifactPublisher(max_pending=2)
    try:
        publisher.submit('/local/staged/step-100.pt', 'oss://my-bucket/checkpoints/step-100.pt',
                         remove_source=False,
                         on_published=lambda: print('checkpoint published'))
        publisher.sync_tree_coalesced('/local/logs', 'oss://my-bucket/logs')
    finally:
        publisher.drain()
    write_text('oss://my-bucket/_DONE', '1')
```

队列满时提交会等待，控制暂存文件的在途数量；submit 不返回 Future，使用 drain 等待并观察失败。
默认 remove_source=True，发布及回调成功后删除源文件；失败即时记录日志并保留源。
回调在后台线程执行，回调失败时目标可能已经上传成功。源文件必须在发布期间保持不变，
临时目录应活到 drain 之后。storage.flush 不替代 publisher.drain，publisher 也不是 with 上下文管理器。

日志树同步会合并同一源目录的在途请求，按文件 size/mtime 跳过未变内容，保留本地源文件，
不删除目标额外文件；这是发布操作，不是完整的双向同步工具。

<a id="integrity"></a>

## 7. 范围读取、create-only、元数据与版本校验

### 只读需要的字节

```python
from oss_io_tool import Path, io_session, write_bytes

root = Path('/tmp/io-demo/ranges')
with io_session() as storage:
    path = root / 'records.bin'
    write_bytes(path, b'HEADER:payload:END')
    assert storage.read_range(path, start=7, length=7) == b'payload'
```

范围不能越过文件末尾，length 必须是正整数。OSS 使用范围请求；
可用于显式索引中已知偏移的裸二进制或未压缩 TAR 成员。open_file.seek 在远端不具有同样的节省下载效果。

### 只创建一次，并存储 OSS 用户元数据

```python
import hashlib
import uuid
from oss_io_tool import Path, io_session, write_object

path = Path('/tmp/io-demo/claims') / (uuid.uuid4().hex + '.bin')  # 也可以是 OSS
payload = b'published once'
with io_session() as storage:
    write_object(path, payload, overwrite=False,
                 metadata={'sha256': hashlib.sha256(payload).hexdigest()})
    try:
        write_object(path, b'overwrite', overwrite=False)
    except FileExistsError:
        print('已存在，未覆盖')
```

OSS 在服务端执行禁止覆盖条件，分片上传也保留条件及 metadata；本地支持原子 create-only，
但 metadata 不持久化。S3 的禁止覆盖/metadata 写入未实现，会报 NotImplementedError。
不要用 exists 再 write 代替原子 create-only。

### 锁定远端版本或本地内容摘要

```python
import hashlib
from oss_io_tool import Path, io_session, read_bytes, read_pinned, write_bytes

path = Path('/tmp/io-demo/pinned.bin')
write_bytes(path, b'fixed input')
expected_sha256 = hashlib.sha256(read_bytes(path)).hexdigest()
assert read_pinned(path, etag=expected_sha256) == b'fixed input'

# OSS：期望 ETag 应来自已保存的 inventory/规划结果，而非把它当成 MD5。
# with io_session() as storage:
#     version = storage.stat('oss://my-bucket/input.bin').etag
#     payload = read_pinned('oss://my-bucket/input.bin', etag=version)
```

OSS 用 If-Match 检查 ETag；本地/S3 这个参数按 SHA-256 核对内容。不要把 S3 ETag 传作 SHA-256，
也不要把分片对象的 ETag 当作 MD5。带 ETag 的 OSS read_pinned 是直接条件请求，绕过常规缓存、预算与计数。
完整可运行版本：[inventory_and_integrity.py](examples/inventory_and_integrity.py)。

<a id="inventories"></a>

## 8. 显式 inventory、远端目录查询与索引

### 管理阶段列一次，消费阶段读清单

```python
import json
from oss_io_tool import Path, io_session, write_text

root = Path('/tmp/io-demo/data')  # OSS 可换为 oss://my-bucket/data
root.mkdir(parents=True, exist_ok=True)
(root / 'one.bin').write_bytes(b'1')
with io_session() as storage:
    rows = list(storage.inventory(root, allow_remote=True))
    write_text('/tmp/io-demo/inventory.jsonl', ''.join(json.dumps(row) + '\n' for row in rows))
```

每行包含 `path/size/etag/mtime_ns`，OSS 自动分页，远端必须显式 allow_remote=True；
S3 administrative inventory 暂未实现。清单不是并发改写环境下的事务快照，业务热路径应消费规划好的稳定清单。
本地 inventory 的 etag 为 None，不会自动生成内容摘要。

### 两种清单格式不要混用

| 清单 | 结构 | 用途 |
| --- | --- | --- |
| `inventory.jsonl` | 每行是完整路径与大小/版本元数据字典 | 规划、审计、按元数据复制/固定版本 |
| `_io_files.json` | 一个相对文件名 JSON 数组 | native_package、IndexedFilesystem 的目录/通配符视图 |

```python
import json
from oss_io_tool import Path, write_text
from oss_io_tool.index import IndexedFilesystem

root = Path('/tmp/io-demo/indexed')
(root / 'configs/model.json').write_text('{}')
(root / 'weights.bin').write_bytes(b'weights')
write_text(root / '_io_files.json', json.dumps(['configs/model.json', 'weights.bin']))
filesystem = IndexedFilesystem(root)
assert filesystem.smart_listdir(root / 'configs') == ['model.json']
assert filesystem.smart_glob(root / 'configs/*.json') == [str(root / 'configs/model.json')]
with filesystem.smart_open(root / 'weights.bin', 'rb') as stream:
    assert stream.read() == b'weights'
```

同样把 root 换成 OSS 即可按清单查询，不调用云列表 API；本地 IndexedFilesystem 直接使用真实目录。
smart_open/smart_glob 等参数是完整路径，不会自动把相对字符串拼到 root。
smart_exists 检查实际对象，不只检查清单。使用已有 JSONL 远端源进行复制：

```python
import json
from oss_io_tool import Path, copy_file, open_file

source = Path('oss://my-bucket/data')
destination = Path('/tmp/io-download')
# inventory 在管理阶段生成，其每行 path 位于 source 下。
with open_file('/path/to/inventory.jsonl', 'r') as stream:
    for line in stream:
        row = json.loads(line)
        copy_file(row['path'], destination / Path(row['path']).relative_to(source))
```

<a id="cache-observability"></a>

## 9. 远端读缓存、下载预算、摘要与统计

```python
from oss_io_tool import io_session, read_bytes

# 需要已有对象；缓存仅在这个会话内存活，不写本地缓存文件。
with io_session(cache_reads=True, max_remote_read_bytes=128 << 20,
                record_writes=True) as storage:
    first = read_bytes('oss://my-bucket/input.bin')
    second = read_bytes('oss://my-bucket/input.bin')
    assert first == second
    print(storage.cache_hits, storage.bytes_read, storage.remote_bytes_read)
    # 调用过保存/复制等写操作后，可在这里保存记录；close 会清空该字典。
    write_records = dict(storage.written_objects)
```

完整远端读取缓存会查询对象大小/时间/ETag 后复用内容，范围读取也能缓存；不是不再访问网络。
缓存没有自动 LRU，预算是**累计下载字节**，不是内存峰值；缓存、编解码、排队写入都另占内存。
本地读取不受这个远端下载预算限制。显式 seed_content 可预置 ETag 对应的 bytes，具体语义见 API 参数表。
统计是特定路径的观测值，并非每一种原生/流/条件 IO 都计数；完整属性与例外见文末 reference。

<a id="format-adapters"></a>

## 10. 图像、NumPy、checkpoint、mesh 与 Mitsuba

### Pillow、imageio、OpenCV

```python
import numpy as np
from PIL import Image
from oss_io_tool import (Path, load_image, save_image, imageio_read, imageio_write,
                         cv2_read, cv2_write)

root = Path('/tmp/io-demo/images')
rgb = np.zeros((8, 9, 3), dtype=np.uint8)
rgb[..., 0] = 255
save_image(Image.fromarray(rgb), root / 'pillow.png')
assert load_image(root / 'pillow.png').size == (9, 8)
imageio_write(root / 'imageio.png', rgb)
np.testing.assert_array_equal(imageio_read(root / 'imageio.png'), rgb)
bgr = rgb[..., ::-1].copy()  # OpenCV 通常使用 BGR，不能当作 RGB 原样比较
assert cv2_write(root / 'opencv.png', bgr)
np.testing.assert_array_equal(cv2_read(root / 'opencv.png'), bgr)
```

load_image 在返回前完成 Pillow 延迟解码；save_image 支持 PNG/JPEG 和后台持久化。
其他格式可用 imageio，或 `image.save(BytesIO(), format='TIFF')` 后 write_bytes；内存流没有可靠后缀时显式传格式。
cv2_read 默认 IMREAD_COLOR，失败可能返回 None；保留深度/alpha 可传 `flags=cv2.IMREAD_UNCHANGED`。
cv2_write 返回编码是否成功，IO 异常仍会抛出。
见 [image_io.py](examples/image_io.py)。

### NumPy NPY / NPZ / 裸数组 / mmap

```python
import numpy as np
from oss_io_tool import Path, numpy_load, open_file, read_array, write_bytes

root = Path('/tmp/io-demo/numpy')
values = np.arange(12, dtype='<f4').reshape(3, 4)
with open_file(root / 'values.npy', 'wb') as stream:
    np.save(stream, values, allow_pickle=False)
np.testing.assert_array_equal(numpy_load(root / 'values.npy', allow_pickle=False), values)
with open_file(root / 'bundle.npz', 'wb') as stream:
    np.savez_compressed(stream, values=values)
with open_file(root / 'bundle.npz', 'rb') as stream:
    with np.load(stream, allow_pickle=False) as archive:
        np.testing.assert_array_equal(archive['values'], values)
write_bytes(root / 'values.raw', values.tobytes())
with open_file(root / 'values.raw', 'rb') as stream:
    flat = read_array(stream, '<f4', count=values.size)
    np.testing.assert_array_equal(flat.reshape(values.shape), values)
# Linux：numpy_load(root / 'values.npy', mmap_mode='r', allow_pickle=False)
```

本包没有 numpy_save；使用 np.save/np.savez + open_file/BytesIO。
read_array 从流当前位置读取并返回独立副本，不自动恢复 shape，替代只能直接读本地 fd 的 np.fromfile。
NPZ 是懒对象，必须在 stream/归档关闭前取出需要的成员。mmap 建议用于 NPY，压缩 NPZ 不能获得相同映射行为。
numpy_load 的路径 + mmap_mode 会先完整读入 memfd，**包括本地路径**；这不是远端零下载或常量内存方案。
如果需要直接映射本地原文件，使用 NumPy 自带 `np.load(local_path, mmap_mode='r')`。
见 [numpy_io.py](examples/numpy_io.py)，可加 `--mmap`。

### PyTorch 与 safetensors

```python
import torch
from safetensors.torch import save as safetensors_save_bytes
from oss_io_tool import Path, io_session, torch_load, torch_save, safetensors_load, write_bytes

root = Path('/tmp/io-demo/checkpoints')
state = {'weight': torch.arange(12, dtype=torch.float32)}
with io_session():
    future = torch_save(state, root / 'weights.pt', asynchronous=True)
    future.result()
    restored = torch_load(root / 'weights.pt', map_location='cpu', weights_only=True)
    torch.testing.assert_close(restored['weight'], state['weight'])
    write_bytes(root / 'weights.safetensors', safetensors_save_bytes(state))
    torch.testing.assert_close(safetensors_load(root / 'weights.safetensors')['weight'], state['weight'])
```

torch_load 的普通路径用文件流；本地 mmap 直接用原文件，远端 mmap 用 memfd，需 Linux。
已有文件对象可直接传给 torch_load/torch_save；文件对象保存不创建本包后台写入任务。
safetensors_load 目前本地和远端都经 memfd，均需 Linux；没有内置 safetensors_save，使用库返回的 bytes 写入。
大 checkpoint 已在本地时用 copy_file，避免 torch_save 再序列化、占用额外内存。
见 [checkpoint_io.py](examples/checkpoint_io.py)，可加 `--mmap`。

### trimesh：单文件 GLB 与关联 buffer/texture

```python
import trimesh
from oss_io_tool import Path, load_trimesh, write_bytes

root = Path('/tmp/io-demo/meshes')
scene = trimesh.Scene(trimesh.creation.box())
write_bytes(root / 'box.glb', scene.export(file_type='glb'))
loaded = load_trimesh(root / 'box.glb', force='scene', process=False)
assert len(loaded.geometry) == 1
# glTF/OBJ 的外部文件可按相对位置一并上传，load_trimesh 的 resolver 会经统一 IO 加载。
```

本包没有网格保存函数；trimesh.export 后写 bytes 或把导出的 glTF 文件字典逐个写入。
需枚举未知关联文件的格式/插件仍受云端禁止隐式列表限制，此时提供显式文件清单/原生目录。
完整 GLB/glTF 多文件例子：[mesh_io.py](examples/mesh_io.py)。

### Mitsuba Bitmap：EXR 与 PNG/JPEG

```python
import numpy as np
import mitsuba as mi
from oss_io_tool import Path, load_bitmap, write_bitmap

mi.set_variant('scalar_rgb')
root = Path('/tmp/io-demo/mitsuba')
linear = mi.Bitmap(np.full((3, 4, 3), 0.25, dtype=np.float32))
write_bitmap(linear, root / 'linear.exr')
assert np.array(load_bitmap(root / 'linear.exr')).shape == (3, 4, 3)
display = linear.convert(mi.Bitmap.PixelFormat.RGB, mi.Struct.Type.UInt8, True)
write_bitmap(display, root / 'display.png')
```

适配器通过 Mitsuba MemoryStream 编码，不需要磁盘中转；调用方先选择已安装环境支持的 variant。
线性/显示空间和像素类型由调用方决定，工具不会自动改变数据含义。
write_bitmap 支持 PNG/JPEG/OpenEXR 和后台持久化。见 [mitsuba_io.py](examples/mitsuba_io.py)。

<a id="native-memory"></a>

## 11. 第三方库必须使用自带 IO 时：BytesIO、memfd、tmpfs

标准类型名是 **`io.BytesIO`**。先看第三方接口接受什么，再选适配方式：

| 第三方接口 | 方案 | 原生路径与限制 |
| --- | --- | --- |
| 接受 fileobj/stream | `open_file` / `BytesIO` | 跨平台；没有可供 fileno 使用的真实 fd |
| 必须自己 open/mmap(filename) | `memory_file` | Linux `/proc/self/fd/N`；同进程、无真实后缀 |
| 必须后缀、真实目录、原生输出/rename、子进程 | `memory_directory` | `/dev/shm/...` 真正 tmpfs 文件 |
| 已知多个相对输入文件、本进程读取 | `native_directory/native_package` | tmpfs 目录 + 指向本进程 memfd 的链接 |
| 原生路径要跨多层函数使用 | `native_file_scope/native_path/@native_files` | 将生命周期提升到整个同步调用范围 |
| 库与内存路径不兼容且允许写磁盘 | `local_file` / `native_path(memory=False)` | 显式临时磁盘 fallback |

### 文件对象与 BytesIO

```python
import io
from PIL import Image
from oss_io_tool import Path, read_bytes, write_bytes

root = Path('/tmp/io-demo/bytesio')
with io.BytesIO() as buffer:
    Image.new('RGB', (4, 3), 'red').save(buffer, format='PNG')
    write_bytes(root / 'image.png', buffer.getvalue())
with io.BytesIO(read_bytes(root / 'image.png')) as buffer:
    image = Image.open(buffer)
    image.load()  # 关闭输入流前完成延迟解码
assert image.size == (4, 3)
```

只接受文件名、fileno、mmap 的 C/C++ 接口不能直接传 BytesIO。

### 本进程的原生文件名：memfd 输入与输出

```python
from pathlib import Path as LocalPath
from oss_io_tool import Path, memory_file, read_bytes, write_bytes

root = Path('/tmp/io-demo/native')
write_bytes(root / 'input.bin', b'input')
with memory_file(read_bytes(root / 'input.bin'), name='input.bin') as filename:
    # 替换成 native_library.load(filename)。
    assert LocalPath(filename).read_bytes() == b'input'
with memory_file(name='output.bin') as filename:
    # 替换成 native_library.save(filename)，要求直接写入而非旁路 rename。
    LocalPath(filename).write_bytes(b'native output')
    write_bytes(root / 'output.bin', LocalPath(filename).read_bytes())
```

name 是调试名，实际路径没有 `.bin` 后缀。上下文退出后 fd 关闭、路径失效；
不要把 `/proc/self/fd/N` 传给外部进程，它表示各自进程的 fd 且被设置 close-on-exec。
已建立的 mmap 可能继续有效，但库之后重开原路径会失败；scope 要覆盖所有延迟读取。

### 保留扩展名、相对目录、原生输出或子进程：tmpfs

```python
import subprocess
import sys
from pathlib import Path as LocalPath
from oss_io_tool import Path, memory_directory, read_bytes, write_bytes

root = Path('/tmp/io-demo/native')
with memory_directory() as directory:
    local = LocalPath(directory)
    (local / 'input.bin').write_bytes(read_bytes(root / 'input.bin'))
    # 示例子进程使用自己的本地 IO；真实使用可替换为转换器/模型工具。
    subprocess.run([sys.executable, '-c',
                    'from pathlib import Path; import sys; '
                    'p=Path(sys.argv[1]); (p/"output.bin").write_bytes((p/"input.bin").read_bytes())',
                    directory], check=True)
    write_bytes(root / 'converted.bin', (local / 'output.bin').read_bytes())
```

目录及文件都在 `/dev/shm`，适合要求真实文件、扩展名或“临时文件 + rename”保存的库。
Docker/容器需提供足够 shm 容量；memfd 和 tmpfs 都受内存/cgroup 限制，不能当成无限内存。
内存设施不可用时工具报错，不会偷偷写磁盘。

### 多文件原生输入与函数调用范围

```python
from pathlib import Path as LocalPath
from oss_io_tool import (Path, native_directory, native_file_scope, native_path, native_files)

root = Path('/tmp/io-demo/native')
with native_directory(root, ['input.bin', 'output.bin']) as directory:
    assert (LocalPath(directory) / 'input.bin').read_bytes() == b'input'

with native_file_scope():
    filename = native_path(root / 'input.bin')
    prepared = native_path('prepared.bin', data=b'already prepared')
    assert LocalPath(filename).read_bytes() == b'input'
    assert LocalPath(prepared).read_bytes() == b'already prepared'

@native_files
def load_with_native_io(path):
    return LocalPath(native_path(path)).read_bytes()
```

native_directory 的文件是指向本进程 memfd 的 symlink，适合输入读取，不适合子进程、可写目录或禁止 symlink 的库。
原生 scope/decorator 适用于同步执行；不要装饰异步函数/生成器来覆盖尚未执行的延迟读取。

### 显式允许磁盘 fallback

```python
from pathlib import Path as LocalPath
from oss_io_tool import local_file, native_file_scope, native_path

with local_file(b'input', name='input.bin') as filename:
    assert LocalPath(filename).suffix == '.bin'
with native_file_scope():
    filename = native_path('input.bin', data=b'input', memory=False)
    assert LocalPath(filename).read_bytes() == b'input'
```

fsspec 的 `memory://` 是 Python 层虚拟文件系统，原生 open 不认识它；只有第三方库明确支持相应协议时才可使用。
完整版本：[memory_adapters.py](examples/memory_adapters.py)、[native_scopes.py](examples/native_scopes.py)。

## 12. 使用边界、错误处理与命令行

用户无需选择 OSS 上传方式：达到 64 MiB 自动分片，默认 64 MiB 分片，SDK 处理重试、校验、合并及失败时尝试清理。
分片失败不会触发发布成功回调。它解决上传协议限制，不改变 read_bytes、远端 open_file 和格式编码的整对象内存占用。

显式等待 Future、flush 或 drain 观察异常。可捕获 FileNotFoundError、FileExistsError、RemoteListingError 等明确错误；
权限、网络、编解码和其他 SDK 异常会传播，不应一律当作“文件不存在”。
更完整的限制、容量和上传清理说明见[使用边界与禁忌](docs/pitfalls.md)。

```bash
oss-io copy ./result.bin oss://my-bucket/result.bin
oss-io copy oss://my-bucket/result.bin ./download.bin
python -m oss_io_tool copy ./local-model oss://my-bucket/model --recursive
python -m oss_io_tool inventory oss://my-bucket/prefix > inventory.jsonl
# --recursive 需要本地源；远端源使用显式 inventory 循环 copy_file。
# python -m nsl_io ... 是兼容入口。
```

<a id="examples"></a>

## 13. 按功能分类的可运行 examples

安装后从仓库根目录执行。脚本默认写入 `/tmp/oss-io-example/<功能>`，
支持 `--root oss://my-bucket/examples/<功能>`，远端运行需要相应 SDK/凭据。
除表中特别说明的行政清单/条件写入外，也可使用 S3 root；远端示例会实际读写指定前缀。

| 分类 | 脚本与运行方式 | 安装 / 平台 |
| --- | --- | --- |
| 基础字节、文本、同步/后台 | `python examples/basic.py` | 核心 |
| JSON、JSONL、CSV、gzip、ZIP、TAR | `python examples/structured_formats.py` | 核心；`--yaml` 另需 PyYAML |
| 线程池、并发读、有限批次 | `python examples/threaded_io.py` | 核心 |
| asyncio | `python examples/asyncio_io.py` | 核心 |
| 元数据、inventory、索引、range、pinned/create-only、缓存统计 | `python examples/inventory_and_integrity.py` | 核心 / oss；仅本地或 OSS root |
| 单文件/目录复制、模型包、发布队列、日志树 | `python examples/copy_and_publish.py` | 核心；远端 native_package 需 Linux |
| 图像 | `python examples/image_io.py` | images |
| NumPy、NPZ、裸二进制 | `python examples/numpy_io.py` | arrays；`--mmap` 需 Linux |
| PyTorch、safetensors | `python examples/checkpoint_io.py` | torch；safetensors_load 需 Linux，可选 `--mmap` |
| GLB、带外部 buffer 的 glTF | `python examples/mesh_io.py` | mesh |
| EXR/PNG、Mitsuba MemoryStream | `python examples/mitsuba_io.py` | mitsuba + arrays |
| BytesIO、memfd、tmpfs、子进程、原生目录 | `python examples/memory_adapters.py` | 核心，Linux |
| native scope/decorator、预置 bytes、磁盘 fallback | `python examples/native_scopes.py` | 核心，Linux |

[examples/README.md](examples/README.md) 提供逐项运行命令、前置条件和输出说明。
例如 `python examples/numpy_io.py --root oss://my-bucket/examples/numpy --mmap`。

```bash
python -m pip install -e '.[test]' build
python -m pytest
python -m build
```

回归测试使用真实 SDK 请求模型和模拟 OSS，不需要云端凭据；大对象测试使用超过 5 GiB 的稀疏文件，
不分配 5 GiB 内存。PyTorch 未安装时其测试跳过；示例中其他可选库需按对应 extra 安装。
来源与兼容迁移见 [EXTRACTION.md](EXTRACTION.md)。

文末 reference 与 [docs/api.md](docs/api.md) 内容一致，覆盖全部公开接口，按功能查询签名、返回值与限制。

## API reference

本页按功能列出当前实现的全部公开函数、类和方法。主模块函数从 `oss_io_tool` 导入；
子模块的导入位置会单独标注。`nsl_io` 是兼容别名，共享同一个默认 IO 与会话。
签名中的 `*` 后参数只能通过关键字传入；`**kwargs` 透传给对应第三方库。
`path` 通常接受字符串或 `os.PathLike`，云路径应使用工具的 `Path` 保留 URI。

### 1. 会话、资源管理与 IO 配置

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

### 2. 字节、文本、文件流与对象状态

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

### 3. asyncio、线程池与批次等待

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

### 4. URI 路径与本地目录操作

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

### 5. 复制、范围读取与条件发布

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

### 6. 管理清单与 IndexedFilesystem

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

### 7. BytesIO、memfd、tmpfs 与原生库生命周期

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

### 8. 图像、数组、模型与几何格式适配器

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

### 9. 有背压的 ArtifactPublisher

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

### 10. 命令行、异常与返回值约定


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

### 11. 观测指标、注入 sink 与环境变量


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
