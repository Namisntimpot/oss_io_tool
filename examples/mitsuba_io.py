"""Mitsuba EXR/PNG through MemoryStream. Install .[mitsuba,arrays]."""
import argparse
import numpy as np
import mitsuba as mi

from oss_io_tool import Path, io_session, load_bitmap, write_bitmap


def main(root):
    root = Path(root)
    mi.set_variant('scalar_rgb')
    image = np.full((3, 4, 3), 0.25, dtype=np.float32)
    with io_session():
        future = write_bitmap(mi.Bitmap(image), root / 'linear.exr', asynchronous=True)
        future.result()
        np.testing.assert_allclose(np.array(load_bitmap(root / 'linear.exr')), image)
        png = mi.Bitmap(image).convert(mi.Bitmap.PixelFormat.RGB, mi.Struct.Type.UInt8, True)
        write_bitmap(png, root / 'display.png')
        assert np.array(load_bitmap(root / 'display.png')).shape == (3, 4, 3)
    print(f'Mitsuba MemoryStream IO passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/mitsuba')
    main(parser.parse_args().root)
