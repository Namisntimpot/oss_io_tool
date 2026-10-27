# 按功能运行示例

先在仓库根目录安装 `python -m pip install -e .`。下面的脚本默认写入
`/tmp/oss-io-example/<功能>`，每个脚本都会自行生成输入，不要求下载测试数据。
参数 `--root` 接受本地路径、OSS URI 或 S3 URI；远端运行会实际读写指定前缀。

```bash
python -m pip install -e '.[oss]'
python examples/basic.py --root oss://my-bucket/examples/basic
# S3 需安装 .[s3] 并配置 megfile 使用的凭据。
```

## 字节、文本、格式和并发

| 脚本 | 演示内容 | 运行命令 |
| --- | --- | --- |
| [basic.py](basic.py) | 文本、后台写、Future、线程会话传播、复制、完成标记 | `python examples/basic.py` |
| [structured_formats.py](structured_formats.py) | JSON、JSONL、CSV、可信 pickle、gzip、ZIP、TAR | `python examples/structured_formats.py` |
| [threaded_io.py](threaded_io.py) | 有限批次后台写、并发读、submit_io、wait_futures | `python examples/threaded_io.py` |
| [asyncio_io.py](asyncio_io.py) | asyncio.gather、异步文本/字节读写、async_flush | `python examples/asyncio_io.py` |

YAML 为可选演示：安装 `python -m pip install PyYAML` 后执行
`python examples/structured_formats.py --yaml`。JSONL/CSV 每次写完整对象，不使用远端追加。

## 管理、完整性和发布

| 脚本 | 演示内容 | 运行命令 / 前置条件 |
| --- | --- | --- |
| [inventory_and_integrity.py](inventory_and_integrity.py) | 存在性/元数据、range、行政 inventory、IndexedFilesystem、pinned read、create-only、删除、缓存及写入摘要 | `python examples/inventory_and_integrity.py`；仅本地或 OSS root，S3 不支持其中的行政 inventory/create-only 操作 |
| [copy_and_publish.py](copy_and_publish.py) | 单文件、目录、模型包清单、native_package、ArtifactPublisher、日志树合并 | `python examples/copy_and_publish.py`；远端 native_package 需 Linux memfd/tmpfs |

inventory 例子保存 JSONL 元数据清单以及 `_io_files.json` 相对文件名数组，两者分别用于管理和目录视图。
create-only 使用新的唯一文件名，演示成功、覆盖被拒绝后删除该对象；其他输出留在指定 root。
发布队列的例子会在 drain 后检查默认的源 checkpoint 删除行为，日志源文件则保留到临时目录退出。

## 图像、数组和模型格式

| 脚本 | 安装与运行 | 演示内容 |
| --- | --- | --- |
| [image_io.py](image_io.py) | `python -m pip install -e '.[images]'`；`python examples/image_io.py` | Pillow 后台保存、imageio、OpenCV BGR/RGB |
| [numpy_io.py](numpy_io.py) | `python -m pip install -e '.[arrays]'`；`python examples/numpy_io.py` | NPY、压缩 NPZ、裸数组/read_array；Linux 可加 `--mmap` |
| [checkpoint_io.py](checkpoint_io.py) | `python -m pip install -e '.[torch]'`；`python examples/checkpoint_io.py` | 后台 torch_save、weights_only/map_location、safetensors 字节保存/原生加载；Linux 可加 `--mmap` |
| [mesh_io.py](mesh_io.py) | `python -m pip install -e '.[mesh]'`；`python examples/mesh_io.py` | GLB、包含外部 buffer 的多文件 glTF |
| [mitsuba_io.py](mitsuba_io.py) | `python -m pip install -e '.[mitsuba,arrays]'`；`python examples/mitsuba_io.py` | scalar_rgb、浮点 EXR、显示 PNG、MemoryStream |

checkpoint 示例调用的 safetensors_load 即使处理本地路径也使用 memfd，因此整个示例需要 Linux。
可选库没有安装时对应示例无法运行；它们不会被普通本地字节 IO 自动导入。
图像 codecs、Mitsuba variant 或 mesh 插件还需安装环境本身支持相应格式。

## 原生库与内存

| 脚本 | 演示内容 | 运行命令 |
| --- | --- | --- |
| [memory_adapters.py](memory_adapters.py) | BytesIO/ZIP、memfd 文件名、真实 tmpfs 文件、子进程自己的 IO、显式 native_directory 清单 | `python examples/memory_adapters.py` |
| [native_scopes.py](native_scopes.py) | native_file_scope、native_path、@native_files、预置 bytes、显式磁盘 fallback | `python examples/native_scopes.py` |

这两项完整示例需要 Linux `/proc/self/fd`、memfd 和 `/dev/shm` tmpfs。
BytesIO 部分本身跨平台。memory_adapters 不写持久磁盘中转文件；native_scopes 专门演示的
local_file/memory=False 会显式使用临时磁盘，并在上下文退出后删除。

每个脚本成功后输出 roundtrip/passed 提示。完整使用说明与 API 签名见
[README 使用指南](../README.md)和[分类 API reference](../docs/api.md)。
