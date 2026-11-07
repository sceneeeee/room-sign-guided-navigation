"""Batch OCR a directory, preserving one JSON record per frame."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from roomnav.ocr import read_text

EXTS={'.jpg','.jpeg','.png','.webp'}
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('directory',type=Path)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    files=sorted(x for x in args.directory.iterdir() if x.suffix.lower() in EXTS)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('w',encoding='utf8') as f:
        for i,p in enumerate(files,1):
            row={'frame_id':p.stem,'image':str(p),'raw_ocr':read_text(p)}
            f.write(json.dumps(row,ensure_ascii=False)+'\n')
            print(f'[{i}/{len(files)}] {p.name}: {row["raw_ocr"]}')
if __name__=='__main__': main()
