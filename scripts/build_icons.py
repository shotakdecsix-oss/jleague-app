"""
PWA用のアイコン(PNG + SVG)を icons/ に生成する。

なぜPNGが要るか:
    icons/icon.svg だけでは iOS Safari の apple-touch-icon が機能しない
    (iOSはapple-touch-iconにSVGを受け付けず、ホーム画面のアイコンがページの縮小版になる)。
    AndroidのインストールバナーとMaskable対応にも実寸のPNGが要る。

デザインについて:
    赤地に白い「J」、左下に黒の斜め。白・赤・黒の3色。
    **Jリーグの公式ロゴは登録商標なので、それに似せた形は使っていない。**
    配色だけを借りた独自の図案にしてある(公式アプリと誤認されないため)。
    斜めの角度は、Jの文字に黒がかからない高さ(DIAG_Y)にしてある。重なると読みにくく、
    16px程度まで縮んだときに字が潰れる。

    小さいサイズでの見え方を優先している。16pxではもう字は読めないので、
    「赤と黒の斜め分割」というシルエットで見分けがつくことを狙っている。

生成するもの:
    icons/icon.svg              ベクタ対応ブラウザ向け(タブのファビコンの初期値)
    icons/icon-180.png          apple-touch-icon (iOS)。iOS側が角を丸めるので角丸にせず、
                                透明部分も作らない(透明にするとiOSが黒で埋める)
    icons/icon-192.png          Android/Chrome の purpose="any"
    icons/icon-512.png          同上(スプラッシュ用)
    icons/icon-maskable-512.png purpose="maskable"。端末が最大20%を切り落とすので、
                                文字を中央の安全域に収まるよう小さめに描く

    なお、ブラウザのタブのファビコンは index.html の applyFavicon() が選択中のクラブの色で
    動的に差し替える。ここで作るのは、その初期値とホーム画面用のアイコン。
    ホーム画面のアイコンは追加した時点で固定されるため、クラブごとの出し分けはできない。

色を変えたいとき:
    下の3色を書き換えて再実行する(SVGとPNGが揃って作り直される)。
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

RED = "#e60012"
BLACK = "#141414"
WHITE = "#ffffff"
GLYPH = "J"

# 図案のパラメータ(いずれも一辺に対する比率)
DIAG_Y = 0.58      # 黒の斜めが左辺のどの高さから始まるか。下げるほど黒が減る
GLYPH_Y = 0.46     # 文字の中心の高さ。斜めにかからないよう少し上に置く
GLYPH_SIZE = 0.60  # 文字の大きさ
GLYPH_SIZE_MASKABLE = 0.44  # maskableは切り落とされるぶん小さく

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
    """一辺sizeの不透明な正方形に、赤地・黒の斜め・白い文字を描く。"""
    img = Image.new("RGB", (size, size), RED)
    d = ImageDraw.Draw(img)
    # 左辺のDIAG_Yの高さから右下の角へ引いた三角形
    d.polygon([(0, size), (0, int(size * DIAG_Y)), (size, size)], fill=BLACK)
    # anchor="mm" は文字の水平・垂直中央を指定座標に合わせる指定。
    # フォントごとのベースラインの違いを自前で補正するより崩れにくい。
    d.text((size / 2, int(size * GLYPH_Y)), GLYPH,
           font=load_font(int(size * glyph_ratio)), fill=WHITE, anchor="mm")
    return img


def write_svg() -> None:
    """PNGと同じ図案のSVG。タブのファビコンの初期値になる。"""
    S = 192
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}" width="{S}" height="{S}">\n'
        f'  <rect width="{S}" height="{S}" fill="{RED}"/>\n'
        f'  <path d="M0 {S} L0 {int(S * DIAG_Y)} L{S} {S} Z" fill="{BLACK}"/>\n'
        f'  <text x="{S // 2}" y="{int(S * GLYPH_Y)}" text-anchor="middle" dominant-baseline="central"\n'
        f'        font-family="-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif"\n'
        f'        font-size="{int(S * GLYPH_SIZE)}" font-weight="700" fill="{WHITE}">{GLYPH}</text>\n'
        '</svg>\n'
    )
    out = ICONS_DIR / "icon.svg"
    out.write_text(svg, encoding="utf-8")
    print(f"[info] 生成: icons/icon.svg ({out.stat().st_size:,} bytes)")


def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    write_svg()
    targets = [
        ("icon-180.png", 180, GLYPH_SIZE),
        ("icon-192.png", 192, GLYPH_SIZE),
        ("icon-512.png", 512, GLYPH_SIZE),
        ("icon-maskable-512.png", 512, GLYPH_SIZE_MASKABLE),
    ]
    for name, size, ratio in targets:
        out = ICONS_DIR / name
        draw_icon(size, ratio).save(out, format="PNG", optimize=True)
        print(f"[info] 生成: icons/{name} ({size}x{size}, {out.stat().st_size:,} bytes)")
    print("\n[info] アイコンの生成完了。色を変えた場合は manifest と index.html の theme-color も直すこと")


if __name__ == "__main__":
    main()
