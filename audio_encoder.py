"""Convert browser-decoded PCM WAV to the game's Wwise Vorbis media format."""
import io
import struct
import subprocess
import tempfile
import wave
from pathlib import Path


def convert(wav_bytes, template):
    with wave.open(io.BytesIO(wav_bytes)) as audio:
        input_channels = audio.getnchannels()
        input_rate = audio.getframerate()
        if audio.getsampwidth() != 2 or audio.getnchannels() not in (1, 2):
            raise ValueError('音频必须为单声道或双声道的 16 位 PCM')
        if not 0 < audio.getnframes() / audio.getframerate() <= 120:
            raise ValueError('语音长度须在 0 到 120 秒之间')
    offset = template.index(b'fmt ') + 8
    codec, channels, rate = struct.unpack_from('<HHI', template, offset)
    if codec != 0xffff:
        raise ValueError('此语音槽不是 Wwise Vorbis，暂不支持自动转换')
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='craft-wem-') as folder:
        source = Path(folder) / 'source.wav'
        target = Path(folder) / 'output.wem'
        source.write_bytes(wav_bytes)
        command = [str(root / 'audio' / 'wav2wem.exe'), str(source), '-o', str(target), '-q', '4']
        if input_rate != rate:
            command.extend(['-resample', str(rate)])
        if channels == 1 and input_channels == 2:
            command.append('-downmix')
        result = subprocess.run(command, capture_output=True, timeout=90, creationflags=0x08000000)
        if result.returncode or not target.is_file():
            raise ValueError('WEM 编码失败：' + result.stderr.decode('utf8', errors='replace')[-500:])
        verify = subprocess.run([str(root / 'audio' / 'vgmstream-cli.exe'), '-o', str(Path(folder) / 'check.wav'), str(target)], capture_output=True, timeout=30, creationflags=0x08000000)
        if verify.returncode:
            raise ValueError('生成的 WEM 未通过解码验证')
        return target.read_bytes()
