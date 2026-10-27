# oss_io_tool

同一套 Python API 处理本地文件、阿里云 OSS 和 S3。核心仅依赖标准库，云 SDK、NumPy、图像及模型库按功能选装。

从 NSLv2 的 `nsl_io` 提取，成为独立安装包；无需 NSLv2 的源码、`PYTHONPATH`、渲染器或训练环境。
旧的 `import nsl_io` 也可使用，并与 `oss_io_tool` 共享同一个 IO 会话。

## 安装

```bash
git clone <你的仓库地址> oss_io_tool
cd oss_io_tool
python -m pip install -e '.[oss]'
# 纯本地使用：python -m pip install -e .
# 其他选装项：.[s3] / .[arrays] / .[images] / .[torch] / .[mesh] / .[mitsuba]
```

Python >= 3.10。`BytesIO` 适配跨平台；memfd 和 `/dev/shm` 适配需要 Linux。

OSS 凭据只需在运行环境配置一次，业务代码无需接触 SDK：

```bash
export OSS_ACCESS_KEY_ID='<access-key-id>'
export OSS_ACCESS_KEY_SECRET='<access-key-secret>'
export OSS_REGION='cn-hangzhou'
# 临时凭据还需 OSS_SESSION_TOKEN。
# 可选：同地域阿里云内网环境才使用 internal endpoint。
export OSS_ENDPOINT='https://oss-cn-hangzhou.aliyuncs.com'
```

也支持 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`，以及
`OSS_CONFIG_FILE`（默认 `~/.ossutilconfig`）中的 `[Credentials]` / `[default]` 节，
字段为 `accessKeyId`、`accessKeySecret`、`region`、`endpoint`、`stsToken`。
环境变量优先。高级场景可向 `IO(oss_client=...)` / `io_session(oss_client=...)` 注入已配置的 OSS v2 客户端。
S3 由可选的 megfile 管理连接和凭据。

## 本地与 OSS 使用相同的代码

```python
from oss_io_tool import Path, copy_file, io_session, open_file

def run(root):
    root = Path(root)
    with io_session():
        (root / 'config.json').write_text('{"enabled": true}')
        assert (root / 'config.json').read_text() == '{"enabled": true}'
        with open_file(root / 'data.bin', 'wb') as f:
            f.write(b'payload')
        with open_file(root / 'data.bin', 'rb') as f:
            assert f.seekable()
            assert f.read(4) == b'payl'
        copy_file(root / 'data.bin', root / 'copy.bin')

run('/tmp/io-demo')
# run('oss://my-bucket/io-demo')  # 只替换 root
# run('s3://my-bucket/io-demo')
```

路径可以是字符串、工具的 `Path` 或本地 `pathlib.Path`。
拼接云路径要用工具的 `Path`，它会保留 `oss://`；不要用 `pathlib.Path` 拼接云 URI。
`Path.mkdir()` 在对象存储上是空操作，写入会自动处理本地父目录。

字节/文本读写、复制、删除、对象元数据查询提供统一入口；远端目录发现有明确边界：
`glob`、`iterdir`、`is_dir` 不会偷偷扫描云端。需要发现文件时，显式生成 inventory，
或消费已有的文件清单，见[使用边界](docs/pitfalls.md)。

## 同步与后台异步写入

```python
from oss_io_tool import io_session, read_bytes, write_bytes, write_text

with io_session(workers=4) as storage:
    write_text('/tmp/io-demo/status.txt', 'started')  # 同步：完成后才返回
    payload = bytearray(b'snapshot')
    future = write_bytes('/tmp/io-demo/a.bin', payload, asynchronous=True)
    payload[:] = b'changed!'  # 入队时已经复制，后台仍写 snapshot
    future.result()          # 等待单次写入，传播异常
    storage.flush()          # 等待当前会话所有后台写入，传播异常
    write_text('/tmp/io-demo/_DONE', '1')  # 数据全部写完，再发布完成标记
    assert read_bytes('/tmp/io-demo/a.bin') == b'snapshot'
```

`write_bytes` / `write_text` 默认同步并返回 `None`；`asynchronous=True` 返回
`concurrent.futures.Future`。同一 `IO` 实例对同一**字符串路径**的写入按提交顺序执行，
包括后来提交的同步写入。不同路径可以并发，多个进程或多个 IO 实例之间不保证顺序。
读取不会自动等待尚未完成的写入，读回前先等待 Future 或 `flush()`。

`with io_session(...)` 会让模块函数、`Path` 和适配器共用这个会话，退出时自动等待后台写入并清空会话缓存。
`with IO() as storage` 只管理该实例；模块函数仍使用当前会话，因此混用时优先选 `io_session`。
`flush()` 表示 SDK 操作已成功完成或本地文件已关闭并替换，不承诺本地断电后的 fsync 持久性。

### asyncio

```python
import asyncio
from oss_io_tool import (Path, io_session, async_read_text,
                         async_write_text, async_flush)

async def main(root):
    root = Path(root)
    with io_session(workers=4):
        await asyncio.gather(*(
            async_write_text(root / f'{i}.txt', str(i)) for i in range(4)
        ))
        print(await async_read_text(root / '0.txt'))
        await async_flush()

asyncio.run(main('/tmp/io-async'))
# asyncio.run(main('oss://my-bucket/io-async'))
```

这些协程通过线程执行阻塞 IO，不阻塞事件循环；不是 OSS 原生异步 SDK。
后台 Future 在 asyncio 中也可以用 `await asyncio.wrap_future(future)` 等待，不能直接 `await future`。
`async_write_bytes` 被取消后，已经提交的写入仍由会话跟踪，退出/flush 会等待；取消不等于回滚。
异步读取取消后，其线程也可能继续运行；退出会话前应等待相关任务结束。
使用自建线程池时，用 `submit_io(pool, function, ...)` 传播当前会话；普通 `pool.submit` 不会自动传播。

## 让第三方包使用内存

标准类型名是 **`io.BytesIO`**。选择适配方式取决于第三方包接受什么参数：

| 第三方接口 | 使用方式 | 是否有原生路径 | 平台 |
| --- | --- | --- | --- |
| 接受 `fileobj` / stream | `BytesIO` 或 `open_file` | 无 | 跨平台 |
| 必须调用自己的 `open(filename)` / mmap | `memory_file` | `/proc/self/fd/N` | Linux |
| 必须保留扩展名、相对文件树，或交给子进程 | `memory_directory` | `/dev/shm/...` 真文件 | Linux tmpfs |
| 已知多个输入文件，且在本进程读取 | `native_directory` / `native_package` | tmpfs 目录 + memfd 链接 | Linux |
| 内存路径不兼容该库 | `local_file` / `native_path(memory=False)` | 临时磁盘文件 | 跨平台 |

### 1. 能接受文件对象：BytesIO

例如 ZIP、Pillow 和 NumPy 都可以把存储 IO 与格式编解码分开：

```python
import io
import zipfile
from oss_io_tool import Path, read_bytes, write_bytes

root = Path('/tmp/io-demo')  # 也可以是 oss://my-bucket/io-demo
with io.BytesIO() as buffer:
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('hello.txt', 'hello')
    write_bytes(root / 'data.zip', buffer.getvalue())

with io.BytesIO(read_bytes(root / 'data.zip')) as buffer:
    with zipfile.ZipFile(buffer) as archive:
        assert archive.read('hello.txt') == b'hello'
```

Pillow 读取时使用 `Image.open(BytesIO(read_bytes(path)))`，并在流关闭前调用 `image.load()`。
保存时指定 `format='PNG'` 等格式，再将 `buffer.getvalue()` 交给 `write_bytes`。
NumPy 使用 `np.load(BytesIO(...), allow_pickle=False)` 和 `np.save(buffer, array)`。
工具已经提供 `load_image` / `save_image`、`numpy_load`、`torch_load` / `torch_save`、
`imageio_read` / `imageio_write`、`cv2_read` / `cv2_write` 等适配器。

`open_file(path, 'rb')` 已经对远端包装了可 seek 的内存流；可以直接传给 `zipfile.ZipFile` 等库。
本地 `open_file` 则直接打开文件，避免额外复制。不要把只接受文件名的 C/C++ 接口强行传入 BytesIO。

### 2. 只接受本地文件名：memfd

```python
from pathlib import Path as LocalPath
from oss_io_tool import Path, memory_file, read_bytes, write_bytes

root = Path('/tmp/io-demo')
with memory_file(read_bytes(root / 'data.zip'), name='data.zip') as filename:
    # native_library.load(filename) 可使用自己的本地 open/mmap。
    assert LocalPath(filename).read_bytes() == read_bytes(root / 'data.zip')

# 也可处理使用原生文件 IO 的输出。
with memory_file(name='output.bin') as filename:
    LocalPath(filename).write_bytes(b'native output')  # 替换成 native_library.save(filename)
    write_bytes(root / 'output.bin', LocalPath(filename).read_bytes())
```

`name` 只是 memfd 的调试名；实际路径没有 `.zip` 等扩展名。
要求扩展名的库使用下一节的内存目录。上下文退出后文件描述符关闭，路径失效；
懒加载对象必须在作用域内完成读取或复制。不要把 `/proc/self/fd/N` 传给另一个进程，
它表示的是当前进程的 fd，且本工具设置了 close-on-exec。

若调用链层级很深，可在 `with native_file_scope():` 中用 `native_path(remote_path)` 获取路径，
或给入口加 `@native_files` 装饰器，统一管理这些 memfd 的生命周期。

### 3. 必须有扩展名、多个相对文件或子进程：内存文件系统

`/dev/shm` 的 tmpfs 提供真正的文件名和目录结构，普通 `open()` 和第三方原生 IO 都能使用。

```python
from pathlib import Path as LocalPath
from oss_io_tool import Path, memory_directory, read_bytes, write_bytes

root = Path('/tmp/io-demo')
with memory_directory() as directory:
    local = LocalPath(directory)
    (local / 'input.zip').write_bytes(read_bytes(root / 'data.zip'))
    # native_library.convert(str(local / 'input.zip'), str(local / 'output.bin'))
    # subprocess.run([...], check=True) 也必须在作用域内完成。
    (local / 'output.bin').write_bytes(b'converted')
    write_bytes(root / 'converted.bin', (local / 'output.bin').read_bytes())
```

如果库会自己读取 `config.json`、`weights.bin` 等相对输入文件：

```python
from oss_io_tool import native_directory

# 清单只包含明确的相对文件名；不会远程扫描。
with native_directory('oss://my-bucket/model', ['config.json', 'weights.bin']) as local:
    # model = library.load_from_directory(local)
    # 在这里完成所有可能的延迟文件读取。
    pass
```

`native_directory` 的文件是指向本进程 memfd 的符号链接，只适合本进程读取；
要写入、重命名、禁止 symlink，或运行子进程时，请用 `memory_directory` 加真实文件。
`publish_package(local_dir, remote_root)` 会上传文件并最后写入 `_io_files.json`，
之后 `with native_package(remote_root) as local:` 可按清单适配目录加载器。

`fsspec` 的 `memory://` 是 Python 层的虚拟文件系统，原生 `open()` 不认识这个 URI。
它不能直接替代上述 memfd/tmpfs 方案；第三方库必须明确支持相应文件系统协议才可使用它。

## 大文件：调用方无需选择 OSS 上传方式

```python
from oss_io_tool import copy_file, write_bytes

# 已经在内存中的数据：普通接口自动选择简单上传或分片上传。
# write_bytes('oss://my-bucket/result.bin', payload)

# 大型本地文件、OSS 对象复制：通过流传输，无需先读成完整 bytes。
copy_file('/path/to/large-checkpoint.pt', 'oss://my-bucket/checkpoint.pt')
# copy_file('oss://my-bucket/checkpoint.pt', '/path/to/download.pt')
```

`write_bytes`、`write_text`、`open_file` 写入、各格式保存器和 `write_object` 共用上传策略；
`copy_file` 和 CLI 的 OSS 目标同样处理大文件。小对象简单上传，超过内部阈值自动分片，
SDK 负责重试、校验、合并及失败时清理未完成分片。上传完成前不会发布完成标记。

不要为了普通使用编写 SDK 分片逻辑。OSS 单次 PutObject 的 5 GiB 限制、分片数上限、
内存预算以及仍然存在的语义差异记录在[使用边界与禁忌](docs/pitfalls.md)。

## 命令行与开发

```bash
oss-io copy ./result.bin oss://my-bucket/result.bin
python -m oss_io_tool copy ./model oss://my-bucket/model --recursive
python -m oss_io_tool inventory oss://my-bucket/prefix > inventory.jsonl
# python -m nsl_io copy ... 兼容旧入口

python examples/basic.py
python examples/asyncio_io.py
python examples/memory_adapters.py
# 上述示例均支持 --root oss://my-bucket/example

python -m pip install -e '.[test]' build
python -m pytest
python -m build
```

测试使用真实 SDK 请求模型和内存中的 OSS 替身，不需要云端凭据，不访问真实 bucket。
大对象测试通过缩小内部分片阈值以及虚拟尺寸验证路径选择，不会分配 5 GiB 内存。
可选的 PyTorch 测试在未安装时跳过。

完整接口见 [API 速查](docs/api.md)，来源与迁移说明见 [EXTRACTION.md](EXTRACTION.md)。
