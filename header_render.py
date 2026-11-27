"""Align the original card art with the visible artwork in its header."""
import io
import cv2
import numpy as np
from PIL import Image

def header_transform(original, header):
    art = np.asarray(original.convert("RGB"))
    banner = np.asarray(header.convert("RGBA"))
    h, w = banner.shape[:2]
    # Match in display space, compensating for the game's non-square pixels.
    src = cv2.resize(art, (848, 1024))
    ref = cv2.resize(banner, (h * 5, h))
    sift = cv2.SIFT_create()
    pa, da = sift.detectAndCompute(cv2.cvtColor(src, cv2.COLOR_RGB2GRAY), None)
    pb, db = sift.detectAndCompute(cv2.cvtColor(ref, cv2.COLOR_RGBA2GRAY), (ref[:,:,3] > 160).astype('uint8') * 255)
    transform = None
    if da is not None and db is not None and len(da) >= 2:
        pairs = cv2.BFMatcher().knnMatch(db, da, k=2)
        good = [a for a,b in pairs if a.distance < .78*b.distance]
        if len(good) >= 4:
            pts = np.float32([pa[m.trainIdx].pt for m in good])
            dst = np.float32([pb[m.queryIdx].pt for m in good])
            matrix, inliers = cv2.estimateAffine2D(pts, dst, ransacReprojThreshold=2)
            if matrix is not None and inliers.sum() >= max(4,.45*len(good)) and np.linalg.det(matrix[:,:2]) > 0:
                transform = matrix
    if transform is None:
        # Keep the upper centre of the portrait inside the original visible area.
        x,y,bw,bh = cv2.boundingRect((ref[:,:,3] > 16).astype('uint8'))
        scale = max(bw/848, bh/1024, .01)
        transform = np.array([[scale,0,x+(bw-848*scale)/2],[0,scale,y+(bh-1024*scale)*.22]])
    return transform

def make_header(original, header, replacement):
    transform = header_transform(original,header)
    w,h = header.size
    alpha = np.asarray(header.convert("RGBA"))[:,:,3]
    rgb = cv2.resize(np.asarray(replacement.convert("RGB")), (848,1024))
    view = cv2.warpAffine(rgb,transform,(h*5,h),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_REPLICATE)
    result = Image.fromarray(cv2.resize(view,(w,h),interpolation=cv2.INTER_AREA))
    result.putalpha(Image.fromarray(alpha))
    stream = io.BytesIO()
    result.save(stream,'PNG')
    return stream.getvalue()
