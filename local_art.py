"""Local game artwork, cached by the manifest's content hash."""
from pathlib import Path
import threading
import uuid

from PIL import Image, ImageDraw
import UnityPy
import wbtool

_lock = threading.Lock()
HERO = "Prefabs/LeaderSkin/class_1223"


COVER = "108441200"
COVER_EVOLVED = "108441203"
# 首页卡牌入口的取景框（相对进化后立绘的比例）：两人头部到腰部的正方形区域，
# 正方形保证卡片入口不会被压扁，正好裁出人脸所在的画面。
COVER_BOX = (0.25, 0.12, 0.78, 0.65)


def decode_bundle(assets, key, bundle):
    """Return the decrypted Unity bundle a second time; None when unreadable."""
    try:
        path, seed = assets[bundle]
        return UnityPy.load(wbtool.decrypt_bytes(path.read_bytes(), key, seed))
    except Exception:
        return None


def home_cover(cache):
    """Return (face, face_ok) for the home tile; face is an RGBA PIL image."""
    folder = cache / "cover"
    face_path = folder / (COVER_EVOLVED + "-face.png")
    fallback = folder / "fallback.png"
    if face_path.is_file():
        return Image.open(face_path).convert("RGBA"), True
    if fallback.is_file():
        return Image.open(fallback).convert("RGBA"), False
    raise FileNotFoundError("封面资源尚未生成")


def _face_from_texture(texture):
    image = texture.image.convert("RGBA")
    width, height = image.size
    left, top, right, bottom = COVER_BOX
    left, right = int(left * width), int(right * width)
    top, bottom = int(top * height), int(bottom * height)
    side = max(64, min(right - left, bottom - top))
    centre_x = (left + right) // 2
    centre_y = (top + bottom) // 2
    left = max(0, min(width - side, centre_x - side // 2))
    top = max(0, min(height - side, centre_y - side // 2))
    return image.crop((left, top, left + side, top + side))


def write_cover(cache, face, evolved):
    folder = cache / "cover"
    folder.mkdir(parents=True, exist_ok=True)
    if not evolved:
        face.save(folder / "fallback.png")
        return
    big = face.copy()
    big.thumbnail((1200, 1200), Image.LANCZOS)
    big.save(folder / (COVER_EVOLVED + "-face.png"))


def image_file(cache, assets, key, filename, bundle):
    path, seed = assets[bundle]
    folder = cache / "art-v1" / path.name
    result = folder / filename
    with _lock:
        if result.is_file():
            return result
        folder.mkdir(parents=True, exist_ok=True)
        env = UnityPy.load(wbtool.decrypt_bytes(path.read_bytes(), key, seed))
        if bundle == HERO:
            outputs = {}
            for obj in env.objects:
                if obj.type.name not in ("TextAsset", "Texture2D"):
                    continue
                item = obj.read()
                if item.m_Name == "class_1223" and obj.type.name == "Texture2D":
                    outputs["hero.png"] = item.image
                elif item.m_Name in ("class_1223.skel", "class_1223.atlas"):
                    raw = item.m_Script.encode("utf8", "surrogateescape")
                    if item.m_Name.endswith(".atlas"):
                        outputs["hero.atlas"] = raw.replace(b"class_1223.png", b"hero.png")
                    else:
                        outputs[item.m_Name] = raw
            if len(outputs) != 3:
                raise ValueError("游戏立绘资源不完整")
        else:
            image = next(o.read().image for o in env.objects if o.type.name == "Texture2D").convert("RGBA")
            outputs = {filename: image}
            if filename.startswith("mask_"):
                alpha = image.getchannel("A").point(lambda x: 255 if x > 8 else 0)
                ImageDraw.floodfill(alpha, (alpha.width//2, alpha.height//2), 128)
                mask = Image.new("RGBA", image.size, "white")
                mask.putalpha(alpha.point(lambda x: 255 if x == 128 else 0))
                outputs[filename] = mask
        for name, value in outputs.items():
            target = folder / name
            temp = folder / (uuid.uuid4().hex + target.suffix)
            try:
                if isinstance(value, bytes):
                    temp.write_bytes(value)
                else:
                    value.save(temp)
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
        return result
