"""Create an antialiased closed-region ORA fixture for Android fill QA."""
from pathlib import Path
import math
import struct
import zlib
import zipfile
import argparse

parser=argparse.ArgumentParser()
parser.add_argument('--gap',type=int,default=0,help='Width of the opening at the top, in image pixels')
args=parser.parse_args()
if not 0<=args.gap<=64:parser.error('gap must be between 0 and 64')

def png(pixels, size=512):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = b''.join(b'\0' + pixels[y*size*4:(y+1)*size*4] for y in range(size))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB',size,size,8,6,0,0,0)) + chunk(b'IDAT',zlib.compress(raw)) + chunk(b'IEND',b'')

ink = bytearray()
for y in range(512):
    for x in range(512):
        distance = abs(math.hypot(x+.5-256,y+.5-256)-150)
        alpha = round(255 * max(0,min(1,3.5-distance)))
        if y<256 and 256-args.gap//2<=x<256-args.gap//2+args.gap:alpha=0
        ink.extend((0,0,0,alpha))
name=f'ProKrita-Fill-Gap-{args.gap}px.ora' if args.gap else 'ProKrita-Fill-Test.ora'
destination = Path(__file__).resolve().parents[1] / 'android/device-backup' / name
destination.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as output:
    output.writestr('mimetype','image/openraster')
    output.writestr('stack.xml','<image w="512" h="512" name="ProKrita fill test"><stack><layer name="Antialiased outline" src="data/ink.png" opacity="1.0" visibility="visible" composite-op="svg:src-over" selected="true"/><layer name="Background" src="data/background.png" opacity="1.0" visibility="visible" composite-op="svg:src-over"/></stack></image>')
    output.writestr('data/ink.png',png(ink))
    output.writestr('data/background.png',png(bytes((255,255,255,255))*512*512))
print(destination)
