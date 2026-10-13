"""OCR a single image and print lines; install with pip install -e '.[vision]'"""

import argparse
from roomnav.ocr import read_text


def main():
    p = argparse.ArgumentParser()
    p.add_argument("image")
    args = p.parse_args()
    texts = read_text(args.image)
    for line in texts:
        print(line)
    if not texts:
        print("[no text detected]")


if __name__ == "__main__":
    main()
