from astro_triage.backend import get_array_module, asnumpy
from scipy.ndimage import sobel


def background_gradient(image, use_gpu=False):
    xp = get_array_module(use_gpu)

    img = xp.asarray(image)
    gx = sobel(asnumpy(img), axis=1)
    gy = sobel(asnumpy(img), axis=0)

    grad = xp.sqrt(gx**2 + gy**2)
    return float(xp.median(grad))
