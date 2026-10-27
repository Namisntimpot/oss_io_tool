"""Hide OSS single-request limits and multipart bookkeeping from public APIs."""
import io
import math

# Switch well before PutObject's 5 GiB limit, also reducing retry cost.
_SINGLE_PUT_LIMIT = 64 << 20
_MIN_PART_SIZE = 64 << 20
_PART_ALIGNMENT = 1 << 20
_MAX_PART_SIZE = 5 << 30
_MAX_PARTS = 10000


def upload(client, request, source, size, *, filepath=None):
    """Upload bytes or a readable stream; preserve metadata and create-only flags.

    The SDK handles retries, checksums, completion and abort-on-error. Stream
    uploads never create local checkpoint files. The caller owns source lifetime.
    """
    # Reserve one part: the SDK rounds upward when a source has exactly 10,000.
    if size < 0 or size > (_MAX_PARTS - 1) * _MAX_PART_SIZE:
        raise ValueError("object exceeds the OSS multipart size limit")
    if size < _SINGLE_PUT_LIMIT:
        if isinstance(source, bytes):
            request.body = source
        else:
            # Only small objects are materialized. Keep injected SDK-compatible
            # clients and the simple PUT path consistent with byte writes.
            chunks, remaining = [], size
            while remaining:
                chunk = source.read(remaining)
                if not chunk:
                    raise OSError("upload source ended before its declared size")
                chunks.append(chunk)
                remaining -= len(chunk)
            request.body = b"".join(chunks)
        return client.put_object(request)
    # Align upward and stay within the 10,000-part ceiling for very large files.
    part_size = max(_MIN_PART_SIZE,
                    math.ceil(size / (_MAX_PARTS - 1) / _PART_ALIGNMENT) * _PART_ALIGNMENT)
    reader = io.BytesIO(source) if isinstance(source, bytes) else source
    try:
        from alibabacloud_oss_v2 import Uploader
        from alibabacloud_oss_v2.uploader import UploadError
        from alibabacloud_oss_v2.utils import is_seekable
        # SDK executor.map eagerly consumes non-seekable sources. Sequential
        # parts bound memory for OSS response streams instead of queueing all data.
        parallel_num = 3 if is_seekable(reader) else 1
        uploader = Uploader(client, part_size=part_size, parallel_num=parallel_num,
                            leave_parts_on_error=False, enable_checkpoint=False)
        if filepath is not None:
            # SDK upload_file additionally validates the combined object CRC.
            return uploader.upload_file(request, filepath=filepath)
        return uploader.upload_from(request, reader)
    except UploadError as exc:
        detail = exc.unwrap()
        if detail is None:
            raise
        raise detail from exc
    finally:
        if reader is not source:
            reader.close()
