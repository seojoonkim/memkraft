#!/usr/bin/env python3
"""Generate a GitHub-style header banner for MemKraft."""

import struct
import zlib
import os

# Config
WIDTH = 1200
HEIGHT = 300
OUTPUT = os.path.expanduser("~/memcraft/assets/memkraft-banner.png")

# GitHub dark background: #0d1117
BG_R, BG_G, BG_B = 13, 17, 23

# Text color: cyan/teal glow
TEXT_R, TEXT_G, TEXT_B = 0, 200, 200

# Subtitle color
SUB_R, SUB_G, SUB_B = 140, 160, 180

# ── Minimal PNG writer (zero dependencies) ──

def create_png(width, height, pixels):
    """Create PNG from raw RGB pixel data."""
    def chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    
    header = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    
    raw = b''
    for y in range(height):
        raw += b'\x00'  # filter none
        for x in range(width):
            idx = (y * width + x) * 3
            raw += bytes(pixels[idx:idx+3])
    
    idat = chunk(b'IDAT', zlib.compress(raw, 9))
    iend = chunk(b'IEND', b'')
    
    return header + ihdr + idat + iend


# ── Pixel font (5x7 grid, uppercase + digits) ──

FONT = {
    'M': [
        0b10001,
        0b11011,
        0b10101,
        0b10001,
        0b10001,
        0b10001,
        0b10001,
    ],
    'E': [
        0b11111,
        0b10000,
        0b10000,
        0b11110,
        0b10000,
        0b10000,
        0b11111,
    ],
    'K': [
        0b10001,
        0b10010,
        0b10100,
        0b11000,
        0b10100,
        0b10010,
        0b10001,
    ],
    'R': [
        0b11110,
        0b10001,
        0b10001,
        0b11110,
        0b10100,
        0b10010,
        0b10001,
    ],
    'A': [
        0b01110,
        0b10001,
        0b10001,
        0b11111,
        0b10001,
        0b10001,
        0b10001,
    ],
    'F': [
        0b11111,
        0b10000,
        0b10000,
        0b11110,
        0b10000,
        0b10000,
        0b10000,
    ],
    'T': [
        0b11111,
        0b00100,
        0b00100,
        0b00100,
        0b00100,
        0b00100,
        0b00100,
    ],
    'Z': [
        0b11111,
        0b00001,
        0b00010,
        0b00100,
        0b01000,
        0b10000,
        0b11111,
    ],
    'z': [
        0b00000,
        0b00000,
        0b11111,
        0b00010,
        0b00100,
        0b01000,
        0b11111,
    ],
    'e': [
        0b00000,
        0b00000,
        0b01110,
        0b10001,
        0b11111,
        0b10000,
        0b01110,
    ],
    'm': [
        0b00000,
        0b00000,
        0b11010,
        0b10101,
        0b10101,
        0b10001,
        0b10001,
    ],
    'r': [
        0b00000,
        0b00000,
        0b10110,
        0b11001,
        0b10000,
        0b10000,
        0b10000,
    ],
    'a': [
        0b00000,
        0b00000,
        0b01110,
        0b00001,
        0b01111,
        0b10001,
        0b01111,
    ],
    'f': [
        0b00110,
        0b01000,
        0b01000,
        0b11100,
        0b01000,
        0b01000,
        0b01000,
    ],
    't': [
        0b00100,
        0b00100,
        0b01110,
        0b00100,
        0b00100,
        0b00100,
        0b00011,
    ],
    '-': [
        0b00000,
        0b00000,
        0b00000,
        0b11111,
        0b00000,
        0b00000,
        0b00000,
    ],
    'd': [
        0b00001,
        0b00001,
        0b01101,
        0b10011,
        0b10001,
        0b10011,
        0b01101,
    ],
    'p': [
        0b00000,
        0b00000,
        0b11110,
        0b10001,
        0b11110,
        0b10000,
        0b10000,
    ],
    'n': [
        0b00000,
        0b00000,
        0b10110,
        0b11001,
        0b10001,
        0b10001,
        0b10001,
    ],
    'y': [
        0b00000,
        0b00000,
        0b10001,
        0b10001,
        0b01111,
        0b00001,
        0b01110,
    ],
    'c': [
        0b00000,
        0b00000,
        0b01110,
        0b10000,
        0b10000,
        0b10000,
        0b01110,
    ],
    'o': [
        0b00000,
        0b00000,
        0b01110,
        0b10001,
        0b10001,
        0b10001,
        0b01110,
    ],
    'u': [
        0b00000,
        0b00000,
        0b10001,
        0b10001,
        0b10001,
        0b10011,
        0b01101,
    ],
    'g': [
        0b00000,
        0b00000,
        0b01111,
        0b10001,
        0b01111,
        0b00001,
        0b01110,
    ],
    'i': [
        0b00100,
        0b00000,
        0b01100,
        0b00100,
        0b00100,
        0b00100,
        0b01110,
    ],
    's': [
        0b00000,
        0b00000,
        0b01111,
        0b10000,
        0b01110,
        0b00001,
        0b11110,
    ],
    'l': [
        0b01100,
        0b00100,
        0b00100,
        0b00100,
        0b00100,
        0b00100,
        0b01110,
    ],
    'w': [
        0b00000,
        0b00000,
        0b10001,
        0b10001,
        0b10101,
        0b10101,
        0b01010,
    ],
    'h': [
        0b10000,
        0b10000,
        0b10110,
        0b11001,
        0b10001,
        0b10001,
        0b10001,
    ],
    'v': [
        0b00000,
        0b00000,
        0b10001,
        0b10001,
        0b10001,
        0b01010,
        0b00100,
    ],
    'b': [
        0b10000,
        0b10000,
        0b10110,
        0b11001,
        0b10001,
        0b11001,
        0b10110,
    ],
    ' ': [
        0b00000,
        0b00000,
        0b00000,
        0b00000,
        0b00000,
        0b00000,
        0b00000,
    ],
}


def draw_char(pixels, ch, x0, y0, scale, r, g, b, width):
    """Draw a character at (x0, y0) with given scale and color."""
    glyph = FONT.get(ch)
    if not glyph:
        return
    for row_idx, row in enumerate(glyph):
        for col in range(5):
            if row & (1 << (4 - col)):
                for sy in range(scale):
                    for sx in range(scale):
                        px = x0 + col * scale + sx
                        py = y0 + row_idx * scale + sy
                        if 0 <= px < width and 0 <= py < HEIGHT:
                            idx = (py * width + px) * 3
                            # Glow effect: slightly brighter center
                            pixels[idx] = min(255, r + 30)
                            pixels[idx+1] = min(255, g + 30)
                            pixels[idx+2] = min(255, b + 30)


def draw_text(pixels, text, y0, scale, r, g, b, width):
    """Draw centered text."""
    char_w = 5 * scale + max(1, scale // 2)  # tighter spacing
    total_w = len(text) * char_w - scale
    x0 = (width - total_w) // 2
    for i, ch in enumerate(text):
        draw_char(pixels, ch, x0 + i * char_w, y0, scale, r, g, b, width)


def add_scanlines(pixels, width, height):
    """Add subtle scanline effect."""
    for y in range(height):
        if y % 4 == 0:
            for x in range(width):
                idx = (y * width + x) * 3
                pixels[idx] = max(0, pixels[idx] - 5)
                pixels[idx+1] = max(0, pixels[idx+1] - 5)
                pixels[idx+2] = max(0, pixels[idx+2] - 5)


def add_gradient_bar(pixels, width, y_start, y_end, r, g, b):
    """Add a thin gradient accent bar."""
    for y in range(y_start, min(y_end, HEIGHT)):
        alpha = 1.0 - (y - y_start) / max(1, y_end - y_start)
        for x in range(width):
            idx = (y * width + x) * 3
            pixels[idx] = min(255, pixels[idx] + int(r * alpha * 0.3))
            pixels[idx+1] = min(255, pixels[idx+1] + int(g * alpha * 0.3))
            pixels[idx+2] = min(255, pixels[idx+2] + int(b * alpha * 0.3))


def main():
    # Create pixel buffer (RGB)
    pixels = bytearray(WIDTH * HEIGHT * 3)
    
    # Fill background (#0d1117)
    for i in range(WIDTH * HEIGHT):
        pixels[i*3] = BG_R
        pixels[i*3+1] = BG_G
        pixels[i*3+2] = BG_B
    
    # Add subtle gradient at top
    add_gradient_bar(pixels, WIDTH, 0, 4, TEXT_R, TEXT_G, TEXT_B)
    
    # Main title: "MemKraft" — large pixel font
    title_scale = 10
    title_h = 7 * title_scale
    title_y = (HEIGHT - title_h) // 2 - 25
    draw_text(pixels, "MemKraft", title_y, title_scale, TEXT_R, TEXT_G, TEXT_B, WIDTH)
    
    # Subtitle
    sub_scale = 3
    sub_y = title_y + title_h + 20
    draw_text(pixels, "zero-dependency compound memory for ai agents", sub_y, sub_scale, SUB_R, SUB_G, SUB_B, WIDTH)
    
    # Scanline effect
    add_scanlines(pixels, WIDTH, HEIGHT)
    
    # Bottom accent bar
    add_gradient_bar(pixels, WIDTH, HEIGHT - 4, HEIGHT, TEXT_R, TEXT_G, TEXT_B)
    
    # Write PNG
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    png_data = create_png(WIDTH, HEIGHT, pixels)
    with open(OUTPUT, 'wb') as f:
        f.write(png_data)
    
    print(f"Banner generated: {OUTPUT}")
    print(f"Size: {WIDTH}x{HEIGHT}")
    print(f"File: {len(png_data):,} bytes")


if __name__ == '__main__':
    main()
