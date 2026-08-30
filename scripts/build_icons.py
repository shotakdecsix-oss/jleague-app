"""
PWA用のPNGアイコンを icons/ に生成する。

なぜPNGが要るか:
    icons/icon.svg だけでは iOS Safari の apple-touch-icon が機能しない
    (iOSはapple-touch-iconにSVGを受け付けず、ホーム画面に追加したときのアイコンが
     ページのスクリーンショットの縮小版になる)。AndroidのインストールバナーとMaskable対応にも
    実寸のPNGが要る。

生成するもの:
    icons/icon-180.png           apple-touch-icon (iOS)。iOS側が角を丸めるので、
                                 こちらは角丸にせず、透明部分も作らない正方形で描く
                                 (透明にするとiOSが黒で埋める)。
    icons/icon-192.png           Android/Chrome の purpose="any"
    icons/icon-512.png           同上(スプラッシュ用の大きい方)
    icons/icon-maskable-512.png  purpose="maskable"。端末が円や角丸に切り抜くので、
                                 文字は中央の安全域(直径80%)に収まるよう小さめに描く。

なぜ中立色なのか:
    ホーム画面のアイコンとmanifestは静的ファイルで、全員に同じものが配られる。
    追加した時点の内容で固定されるため、選んだクラブに応じて出し分けることはできない
    (JSで<link>を書き換えても、既にホーム画面にあるアイコンには反映されない)。
    特定クラブの色(以前は湘南の#82c039だった)を焼き付けるより、アプリ共通の中立色にしておく。
    クラブ色を反映したいのはブラウザのタブのファビコンで、そちらは index.html の
    applyFavicon() がSVGを組み立てて動的に差し替えている。

色を変えたいとき:
    BG_COLOR を書き換えて再実行するだけ(icon.svg も一緒に作り直される)。
    manifest.webmanifest の theme_color / background_color と、
    index.html の <meta name="theme-color"> の既定値も合わせて直すこと(3箇所ある)。

CLI:
    python scripts/build_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent.parent
ICONS_DIR = BASE_DIR / "icons"

BG_COLOR = "#33404f"   # 全体モードの中立色(NEUTRAL_COLOR)。特定クラブの色を焼き付けない
FG_COLOR = "#ffffff"
GLYPH = "J"

# 太めのサンセリフ。環境に無ければ順に次を試す(Windows/macOS/Linuxのどれでも動くように)。
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit(
        "[error] 太字のTrueTypeフォントが見つからない。FONT_CANDIDATES に手元のフォントのパスを足すこと"
    )


def draw_icon(size: int, glyph_ratio: float) -> Image.Image:
    """
    一辺sizeの不透明な正方形に、中央へGLYPHを描く。
    glyph_ratio は「文字の高さ / 画像の一辺」の目安。maskableだけ小さくする。
    """
    img = Image.new("RGB", (size, size), BG_COLOR)
    draw = ImageDraw.Draw(img)
    font = load_font(int(size * glyph_ratio))
    # anchor="mm" は「文字の水平中央・垂直中央」を指定座標に合わせる指定。
    # フォントごとにベースラインの位置が違うので、自前でbboxから補正するより素直で崩れにくい。
    draw.text((size / 2, size / 2), GLYPH, font=font, fill=FG_COLOR, anchor="mm")
    return img


def write_svg() -> None:
    """
    フォールバック用のSVG(ベクタ対応ブラウザ向け)。PNGと同じ色・同じ字で作り直す。
    以前は手書きのファイルだったので、BG_COLORを変えてもここだけ古い色が残っていた。
    """
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192" width="192" height="192">\n'
        f'  <rect width="192" height="192" rx="42" fill="{BG_COLOR}"/>\n'
        '  <text x="96" y="96" text-anchor="middle" dominant-baseline="central"\n'
        '        font-family="-apple-system,BlinkMacSystemFont,\'Hiragino Kaku Gothic ProN\','
        '\'Noto Sans JP\',sans-serif"\n'
        f'        font-size="112" font-weight="700" fill="{FG_COLOR}">{GLYPH}</text>\n'
        '</svg>\n'
    )
    out = ICONS_DIR / "icon.svg"
    out.write_text(svg, encoding="utf-8")
    print(f"[info] 生成: icons/icon.svg ({out.stat().st_size:,} bytes)")


def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    write_svg()
    targets = [
        ("icon-180.png", 180, 0.62),
        ("icon-192.png", 192, 0.62),
        ("icon-512.png", 512, 0.62),
        # maskableは端末が最大20%を切り落とす。文字を安全域(中央80%)に収めるため小さく描く。
        ("icon-maskable-512.png", 512, 0.44),
    ]
    for name, size, ratio in targets:
        img = draw_icon(size, ratio)
        out = ICONS_DIR / name
        img.save(out, format="PNG", optimize=True)
        print(f"[info] 生成: icons/{name} ({size}x{size}, {out.stat().st_size:,} bytes)")
    print("\n[info] アイコンの生成完了。manifest.webmanifest と index.html の参照を更新すること")


if __name__ == "__main__":
    main()
