"""Pillow RGB, imageio arrays and OpenCV BGR roundtrips. Install .[images]."""
import argparse
import numpy as np
from PIL import Image

from oss_io_tool import (Path, cv2_read, cv2_write, imageio_read, imageio_write,
                         io_session, load_image, save_image)


def main(root):
    root = Path(root)
    rgb = np.arange(8 * 9 * 3, dtype=np.uint8).reshape(8, 9, 3)
    with io_session():
        future = save_image(Image.fromarray(rgb), root / 'pillow.png', asynchronous=True)
        future.result()
        np.testing.assert_array_equal(np.asarray(load_image(root / 'pillow.png')), rgb)
        imageio_write(root / 'imageio.png', rgb)
        np.testing.assert_array_equal(imageio_read(root / 'imageio.png'), rgb)
        bgr = rgb[..., ::-1].copy()
        assert cv2_write(root / 'opencv.png', bgr)
        np.testing.assert_array_equal(cv2_read(root / 'opencv.png'), bgr)
    print(f'Pillow/imageio/OpenCV IO passed: {root}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', default='/tmp/oss-io-example/images')
    main(parser.parse_args().root)
