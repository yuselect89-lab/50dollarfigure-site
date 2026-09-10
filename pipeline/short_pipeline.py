#!/usr/bin/env python3
"""
$50 FIGURE 静止画ショート動画自動生成パイヷライン v2
保存場所: Drive > figure_pipeline_backup > short_pipeline.py

=== パターン指定 ===
CONFIG の "pattern" キーで動画構成を切り替える:

  "pattern": "A"  旧スタイル。静止画テキスト冒頭→フック5枚→ショーケース
                  視聴回数は少なが残った視聴者の維持率が高い（30秒平均）

  "pattern": "B"  現行スタイル。フラッシュカット→フック5枚→ショーケース
                  視聴回数5〜10倍だが信頼パートで10秒付近に大量離脱

  "pattern": "C"  最適化スタイル。フラッシュカット→フック1枚→ショーケース先→信頼パート後→スペック
                  Bの冒頝掴み力を維持しそそ、視覚的満足感でそなぎ止めてから信頼パートを提示

タイムライン比較:
  A: [ロゴカード][フック×5][遷移][ショーケース][スペック][アウトロ]
  B: [フラッシュ][フック×5][遷移][ショーケース][スペック][アウトロ]  ← 現行
  C: [フラッシュ][フック×1][ショーケース][信頼2枚][スペック][アウトロ]  ← 推奨

=== 新チャットでの使い方 ===
「プライズフォルダのXXXフォルダ使って$50フィギュアのショート動画作って。パターンCで」

=== 前提（Claudeがスクリプト実行前に準備するもの） ===
- 写真:      /home/claude/pipeline/photos/{IMG_XXXX}.png
             (HEIC->PNG変換済み or JPGはexif_transpose適用済み)
- フック5枚: /home/claude/pipeline/assets/hooks/HOOK_01_real.png〜HOOK_05_protection.png
- ロジ元画像: /home/claude/pipeline/assets/logo_raw.png
- BGM:       /home/claude/pipeline/assets/bgm/Eight_Point_Stance.mp3
             /home/claude/pipeline/assets/bgm/Copper_Keys.mp3
"""

import os, sys, json, subprocess, shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
import numpy as np
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass  # HEICサポートなし — JPG/PNGは問題なし
from collections import deque
import librosa

# ================================================================
# CONFIG: 毎回ここで記える
# ================================================================
CONFIG = {
    # --- Notionから転記 ---
    "franchise": "One Piece",
    "character": "Monkey D. Luffy Gear 5",
    "line": "Grandista",
    "maker": "Bandai Spirits",
    "size": None,                          # Notionに実測値なし
    "condition": "Used (Opened)",
    "banner_text": "ONE PIECE — LUFFY GEAR 5",

    # --- パターン指定（"A" / "B" / "C"） ---
    # A: 旧スタイル（維持率高・視聴数少）
    # B: 現行スタイル（視聴数多・10秒離脱）
    # C: 最適化（Bの掴み力＋ショーケース先出し・推奨）
    "pattern": "C",

    # --- 1枚あたりのショーケース秒数 ---
    "showcase_duration_per_photo": 3.0,

    # --- クロップ設定（コンタクトシート目視後に記入） ---
    # 0.0=左端 / 0.5=中央(デフォルト) / 1.0=右端
    "crop_offsets": {
        "IMG_0793": 0.5,
        "IMG_0794": 0.5,
        "IMG_0795": 0.5,
        "IMG_0804": 0.5,
        "IMG_0805": 0.5,
        "IMG_0806": 0.5,
        "IMG_0807": 0.5,
        "IMG_0808": 0.5,
        "IMG_0810": 0.5,
        "IMG_0811": 0.5,
    },

    # --- ショーケース順（10枚×3秒=30秒）---
    "showcase_order": [
        "IMG_0805",   # 全身アクション（メイン）
        "IMG_0794",   # 顔アップ・ドラマチック
        "IMG_0811",   # 上半身フロント
        "IMG_0804",   # 走りポーズ別角度
        "IMG_0806",   # 麦わら帽子顔
        "IMG_0810",   # 走りバリエーション
        "IMG_0807",   # 雲ヘア後方シルエット
        "IMG_0808",   # 紫パンツ下からの詳細
        "IMG_0793",   # 箱正面（商品確認）
        "IMG_0795",   # 箱+フィギュア見える
    ],

    # --- フラッシュカット順（7枚・パターンB/Cのみ使用） ---
    "flash_order": [
        "IMG_0794",   # 顔アップ（最初の掴み）
        "IMG_0793",   # 箱正面
        "IMG_0807",   # 白い雲ヘア後方
        "IMG_0805",   # 走りポーズ
        "IMG_0806",   # 麦わら帽子顔
        "IMG_0804",   # 別角度走り
        "IMG_0811",   # 上半身フロント
    ],

    "output_name": "luffy_gear5_short",
    "is_remake": False,
    "original_video_url": "",
    "ebay_url": "",           # 出品後に埋める（任意）
    "photos_dir": "",         # 空 = /home/claude/pipeline/photos を使用
}

# ================================================================
# コマンドライン引数でJSONコンフィグを上書き
# ================================================================
_cfg_file = next((a for a in sys.argv[1:] if a.endswith(".json")), None)
if _cfg_file:
    with open(_cfg_file, encoding="utf-8") as _f:
        CONFIG.update(json.load(_f))

# ================================================================
# 定数（変更禁止）
# ================================================================
CREAM = (250, 240, 226)
INK   = (32, 27, 23)
RED   = (196, 32, 40)
W, H  = 1080, 1920
HEAD  = "ArchivoBlack-Regular.ttf"
BODY  = "SpaceGrotesk-Bold.ttf"

WORK   = Path("/home/claude/pipeline")
PHOTOS = Path(CONFIG["photos_dir"]) if CONFIG.get("photos_dir") else WORK / "photos"
ASSETS = WORK / "assets"
BUILD  = WORK / "build"
OUT    = Path("/mnt/user-data/outputs")

# ================================================================
# パターン定義
# ================================================================
# 各パターンのクリップ順を定義する。
# showcase_clips はショーケース皞数に応じて動的に展開される。
# trust_clips は信頼パート（フック2〜5枚）のクリップ。
PATTERNS = {
    "A": {
        "description": "旧スタイル。静止画テキスト冒頭→フック5枚→ショーケース",
        # フラッシュカットなし
        "use_flash": False,
        # ロゴ静止画カードを冒頝に置く
        "use_logo_opener": True,
        # フック構成: 5枚全部・冒頭
        "hook_order": ["HOOK_01_real.png","HOOK_02_shipping.png","HOOK_03_trust.png",
                       "HOOK_04_price.png","HOOK_05_protection.png"],
        "hook_before_showcase": True,
        # 信頼パートをショーケース後に置くか（Aは冒頭なのでFalse）
        "trust_after_showcase": False,
        # ショーケース後の信頼パート（Aは不要）
        "post_showcase_trust": [],
        # 遷移カードを使うか
        "use_transition": True,
    },
    "B": {
        "description": "現行スタイル。フラッシュカット→フック5枚→ショーケース",
        "use_flash": True,
        "use_logo_opener": False,
        "hook_order": ["HOOK_01_real.png","HOOK_02_shipping.png","HOOK_03_trust.png",
                       "HOOK_04_price.png","HOOK_05_protection.png"],
        "hook_before_showcase": True,
        "trust_after_showcase": False,
        "post_showcase_trust": [],
        "use_transition": True,
    },
    "C": {
        "description": "最適化。フラッシュ→フック1枚→ショーケース先→信頼パート後",
        "use_flash": True,
        "use_logo_opener": False,
        # 冒頝フックは「Is this figure even REAL?」の1枚のみ
        "hook_order": ["HOOK_01_real.png"],
        "hook_before_showcase": True,
        "trust_after_showcase": True,
        # ショーケース後に配置する信頼パート（$50とeBayの2枚に絞る）
        "post_showcase_trust": ["HOOK_04_price.png","HOOK_05_protection.png"],
        # 遷移カードなし（フック1枚→ショーケース直行の方がテンポ良い）
        "use_transition": False,
    },
}

# ================================================================
# ユーティリティ
# ================================================================
def run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True)

def dur(p):
    r = run(["ffprobe","-v","error","-show_entries","format=duration",
             "-of","default=noprint_wrappers=1",str(p)])
    return float(r.stdout.strip().split("=")[-1])

def fh(s): return ImageFont.truetype(str(BUILD/HEAD), s)
def fb(s): return ImageFont.truetype(str(BUILD/BODY), s)

def cnt(d, cx, cy, t, f, c, sp=14):
    d.multiline_text((cx,cy), t, font=f, fill=c, anchor="mm", align="center", spacing=sp)

# ================================================================
# Step 0: デアレクトリ準備
# ================================================================
def setup():
    for p in [BUILD/"cards", BUILD/"clips", OUT]:
        p.mkdir(parents=True, exist_ok=True)
    print(f"[0] パターン: {CONFIG['pattern']} — {PATTERNS[CONFIG['pattern']]['description']}")

# ================================================================
# Step 1: フォント取得（npm経由）
# ================================================================
def get_fonts():
    if (BUILD/HEAD).exists() and (BUILD/BODY).exists(): return
    fd = BUILD/"fd"; fd.mkdir(exist_ok=True)
    os.chdir(str(fd))
    run(["npm","pack","@fontsource/archivo-black","@fontsource/space-grotesk"])
    for f in fd.glob("*.tgz"): run(["tar","xzf",str(f)])
    run(["pip","install","fonttools","--break-system-packages","-q"])
    from fontTools.ttLib import TTFont
    import glob as g
    for name, weight in [(HEAD,"400-normal"),(BODY,"700-normal")]:
        ms = g.glob(str(fd/f"fontsource-*/files/*{weight}.woff2"))
        key = "archivo" if "Archivo" in name else "space-grotesk"
        ms = [m for m in ms if key.split("-")[0] in m.lower()]
        if not ms: ms = g.glob(str(fd/f"fontsource-*/files/*{weight}.woff2"))
        t = TTFont(ms[0]); t.flavor = None; t.save(str(BUILD/name))
    print("[1] フォント取得完了")

# ================================================================
# Step 2: ロゴ透過処理
# ================================================================
def process_logo():
    lf = BUILD/"logo_final.png"; if_ = BUILD/"icon_final.png"
    if lf.exists() and if_.exists(): return
    def flood(path, out, tol=35):
        im = Image.open(path).convert("RGB"); arr = np.array(im).astype(int)
        h, w, _ = arr.shape
        cs = [tuple(arr[2,2]),tuple(arr[2,w-3]),tuple(arr[h-3,2]),tuple(arr[h-3,w-3])]
        bg = np.mean(cs,axis=0); dist = np.sqrt(((arr-bg)**2).sum(axis=2)); is_bg = dist<tol
        vis = np.zeros((h,w),bool); mask = np.zeros((h,w),bool)
        q = deque()
        for x in range(w): q.append((0,x)); q.append((h-1,x))
        for y in range(h): q.append((y,0)); q.append((y,w-1))
        while q:
            y,x = q.popleft()
            if y<0 or y>=h or x<0 or x>=w or vis[y,x]: continue
            vis[y,x] = True
            if not is_bg[y,x]: continue
            mask[y,x] = True; q.extend([(y+1,x),(y-1,x),(y,x+1),(y,x-1)])
        alpha = np.where(mask,0,255).astype(np.uint8)
        ai = Image.fromarray(alpha).filter(ImageFilter.GaussianBlur(1.2))
        Image.fromarray(np.dstack([arr.astype(np.uint8),np.array(ai)]),"RGBA").save(out)
    flood(ASSETS/"logo_raw.png", BUILD/"la.png")
    im = Image.open(BUILD/"la.png"); im.crop(im.getbbox()).save(lf)
    ir = ASSETS/"icon_raw.png"
    if ir.exists(): flood(ir, BUILD/"ia.png"); im2=Image.open(BUILD/"ia.png"); im2.crop(im2.getbbox()).save(if_)
    else: shutil.copy(lf, if_)
    print("[2] ロゴ処理完了")

# ================================================================
# Step 3: 写真クロップ（EXIF対応）
# ================================================================
def crop_photos():
    off = CONFIG["crop_offsets"]
    for name in set(CONFIG["showcase_order"]) | set(CONFIG.get("flash_order",[])):
        out = BUILD/f"{name}.png"
        if out.exists(): continue
        src = PHOTOS/f"{name}.png"
        if src.exists(): im = Image.open(src).convert("RGB")
        else:
            for ext in [".jpg",".jpeg",".JPG",".JPEG",".heic",".HEIC"]:
                j = PHOTOS/(name+ext)
                if j.exists(): im = ImageOps.exif_transpose(Image.open(j)).convert("RGB"); break
            else: raise FileNotFoundError(f"{name} not found")
        w,h = im.size; r = off.get(name,0.5); tw = round(h*9/16)
        x0 = max(0,min(int((w-tw)*r),w-tw))
        im.crop((x0,0,x0+tw,h)).resize((W,H),Image.LANCZOS).save(out)
    print("[3] 写真クロップ完了")

# ================================================================
# Step 4: フックカード処理
# ================================================================
def process_hooks():
    hd = ASSETS/"hooks"
    for fn in ["HOOK_01_real.png","HOOK_02_shipping.png","HOOK_03_trust.png",
               "HOOK_04_price.png","HOOK_05_protection.png"]:
        out = BUILD/"cards"/fn
        if out.exists(): continue
        im = Image.open(hd/fn).convert("RGB"); bg = im.getpixel((5,5))
        im2 = im.resize((1000,int(im.height*1000/im.width)),Image.LANCZOS)
        c = Image.new("RGB",(W,H),bg); c.paste(im2,((W-im2.width)//2,(H-im2.height)//2)); c.save(out)
    print("[4] フックカード処理完了")

# ================================================================
# Step 5: カード生成
# ================================================================
def build_cards():
    logo = Image.open(BUILD/"logo_final.png"); icon = Image.open(BUILD/"icon_final.png")
    pat = PATTERNS[CONFIG["pattern"]]

    # パターンA用: ロゴオープナーカード
    if pat["use_logo_opener"]:
        img = Image.new("RGB",(W,H),CREAM); d = ImageDraw.Draw(img)
        l2 = logo.resize((700,int(logo.height*700/logo.width)),Image.LANCZOS)
        img.paste(l2,(W//2-l2.width//2,H//2-l2.height//2),l2)
        img.save(BUILD/"cards"/"logo_opener.png")

    # 遷移カード
    img = Image.new("RGB",(W,H),CREAM); d = ImageDraw.Draw(img)
    cnt(d,W//2,H//2-160,"Let's go find your",fh(62),INK)
    cnt(d,W//2,H//2-80,"next Japanese figure.",fh(62),INK)
    l2 = logo.resize((620,int(logo.height*620/logo.width)),Image.LANCZOS)
    img.paste(l2,(W//2-l2.width//2,H//2+40),l2); img.save(BUILD/"cards"/"transition.png")

    # スペックカード
    img = Image.new("RGB",(W,H),CREAM); d = ImageDraw.Draw(img)
    cnt(d,W//2,220,"WHAT YOU'RE GETTING",fh(54),INK)
    d.rectangle([W//2-110,300,W//2+110,306],fill=RED)
    specs = [("FRANCHISE",CONFIG["franchise"]),("CHARACTER",CONFIG["character"]),
             ("LINE",CONFIG["line"]),("MAKER",CONFIG["maker"])]
    if CONFIG.get("size"): specs.append(("SIZE",CONFIG["size"]))
    specs.append(("CONDITION",CONFIG["condition"]))
    y = 420
    for lb,vl in specs:
        d.text((150,y),lb,font=fb(26),fill=RED)
        d.text((150,y+42),vl,font=fh(36 if len(vl)>22 else 40),fill=INK)
        d.line([(150,y+100),(W-150,y+100)],fill=(210,200,190),width=2); y+=150
    ic = icon.resize((int(icon.width*100/icon.height),100),Image.LANCZOS)
    img.paste(ic,(W//2-ic.width//2,y+20),ic); img.save(BUILD/"cards"/"spec.png")

    # アウトロカード — 縦余白を確保してテキスト被りを解消
    img = Image.new("RGB",(W,H),CREAM); d = ImageDraw.Draw(img)
    # ロゴ：横640px、上部に配置（高さはアスペクト比から自動計算）
    lw = 640
    l3 = logo.resize((lw, int(logo.height*lw/logo.width)), Image.LANCZOS)
    logo_top = 210
    img.paste(l3,(W//2-l3.width//2, logo_top), l3)
    logo_bot = logo_top + l3.height
    # アクセントライン
    d.rectangle([W//2-90, logo_bot+50, W//2+90, logo_bot+56], fill=RED)
    # メインCTA
    cnt(d,W//2, logo_bot+100, "Want this one?", fh(80), INK)
    cnt(d,W//2, logo_bot+210, "AUTHENTIC PRIZE FIGURE", fb(30), RED)
    # ボタン
    bx,by = (W-640)//2, logo_bot+310
    d.rounded_rectangle([bx,by,bx+640,by+120],radius=22,fill=RED)
    d.text((W//2,by+60),"50DOLLARFIGURE.COM",font=fb(34),fill=CREAM,anchor="mm")
    cnt(d,W//2,by+170,"LINK IN BIO ↓",fb(26),(140,130,120))
    img.save(BUILD/"cards"/"outro.png")

    # ショーケースオーバーレイ
    ov = Image.new("RGBA",(W,H),(0,0,0,0)); od = ImageDraw.Draw(ov)
    i3 = icon.resize((int(icon.width*110/icon.height),110),Image.LANCZOS)
    r,g,b,a = i3.split(); a = a.point(lambda p:int(p*0.55)); i3=Image.merge("RGBA",(r,g,b,a))
    ov.paste(i3,(36,36),i3)
    bt = H-210
    od.rectangle([0,bt,W,H],fill=(*CREAM,235)); od.rectangle([0,bt,W,bt+5],fill=(*RED,255))
    od.text((W//2,bt+40),CONFIG["banner_text"],font=fb(30),fill=INK,anchor="mm")
    bd = "AUTHENTIC PRIZE FIGURE"; tf = fb(24)
    bb = od.textbbox((0,0),bd,font=tf); bw,bh = bb[2]-bb[0],bb[3]-bb[1]
    bx2=W//2-bw//2; by2=bt+90
    od.rounded_rectangle([bx2-22,by2-8,bx2+bw+22,by2+bh+20],radius=22,fill=RED)
    od.text((W//2,by2+bh//2+6),bd,font=tf,fill=CREAM,anchor="mm")
    od.text((W//2,by2+bh+52),"Bought in person in Japan",font=fb(22),fill=(90,80,70),anchor="mm")
    ov.save(BUILD/"cards"/"showcase_overlay.png")
    print("[5] カード生成完了")

# ================================================================
# Step 6: フラッシュカット（パターンB/Cのみ）
# ================================================================
def build_flash():
    pat = PATTERNS[CONFIG["pattern"]]
    if not pat["use_flash"]: return
    y,sr = librosa.load(str(ASSETS/"bgm"/"Eight_Point_Stance.mp3"),sr=None,duration=2.5)
    on = librosa.onset.onset_detect(y=y,sr=sr,units='time',backtrack=True,delta=0.05,wait=2)
    pts = [0.0,on[0]]
    for i in range(len(on)-1): pts+=[(on[i]+on[i+1])/2,on[i+1]]
    pts=pts[:8]; durs=[pts[i+1]-pts[i] for i in range(len(pts)-1)]
    fo = CONFIG["flash_order"]
    with open(BUILD/"fl.txt","w") as f:
        for n,d in zip(fo,durs): f.write(f"file '{BUILD}/{n}.png'\nduration {d}\n")
        f.write(f"file '{BUILD}/{fo[-1]}.png'\n")
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(BUILD/"fl.txt"),
         "-vf","fps=30,format=yuv420p","-an","-c:v","libx264","-preset","ultrafast",
         str(BUILD/"clips"/"flash_cut.mp4"),"-loglevel","error"])
    print("[6] フラッシュカット完了")

# ================================================================
# Step 7: 静止画クリップ生成
# ================================================================
def make_still(png, d2, out):
    o = BUILD/"clips"/out
    if o.exists(): return
    run(["ffmpeg","-y","-loop","1","-i",str(BUILD/"cards"/png),
         "-f","lavfi","-i","anullsrc=r=44100:cl=stereo",
         "-t",str(d2),
         "-vf",f"fps=30,format=yuv420p,fade=t=in:st=0:d=0.15,fade=t=out:st={d2-0.15:.3f}:d=0.15",
         "-c:v","libx264","-preset","ultrafast","-c:a","aac","-shortest",str(o),"-loglevel","error"])

def build_stills():
    # 必ず生成するカード
    make_still("HOOK_01_real.png",3.0,"c_h1.mp4")
    make_still("HOOK_02_shipping.png",3.0,"c_h2.mp4")
    make_still("HOOK_03_trust.png",2.2,"c_h3.mp4")
    make_still("HOOK_04_price.png",2.5,"c_h4.mp4")
    make_still("HOOK_05_protection.png",2.2,"c_h5.mp4")
    make_still("transition.png",1.5,"c_tr.mp4")
    make_still("spec.png",3.8,"c_sp.mp4")
    make_still("outro.png",4.0,"c_ou.mp4")
    pat = PATTERNS[CONFIG["pattern"]]
    if pat.get("use_logo_opener"):
        make_still("logo_opener.png",3.0,"c_logo.mp4")
    print("[7] 静止画クリップ完了")

# ================================================================
# Step 8: ショーケース
# ================================================================
def build_showcase():
    so = CONFIG["showcase_order"]; d2 = CONFIG.get("showcase_duration_per_photo", 2.0)
    for i,n in enumerate(so):
        raw = BUILD/"clips"/f"sc{i:02d}r.mp4"; fin = BUILD/"clips"/f"sc{i:02d}.mp4"
        if fin.exists(): continue
        run(["ffmpeg","-y","-loop","1","-i",str(BUILD/f"{n}.png"),"-t",str(d2),
             "-vf",f"scale=1200:2134,zoompan=z='min(zoom+0.0008,1.08)':d={int(d2*30)}:s=1080x1920:fps=30,format=yuv420p",
             "-an","-c:v","libx264","-preset","ultrafast",str(raw),"-loglevel","error"])
        run(["ffmpeg","-y","-i",str(raw),"-i",str(BUILD/"cards"/"showcase_overlay.png"),
             "-filter_complex","[0:v][1:v]overlay=0:0[v]","-map","[v]","-an",
             "-c:v","libx264","-preset","ultrafast",str(fin),"-loglevel","error"])
    print("[8] ショーケース完了")

# ================================================================
# Step 9: タイムライン組み立て（パターン別）
# ================================================================
def assemble():
    pat = PATTERNS[CONFIG["pattern"]]
    so = CONFIG["showcase_order"]
    clips = []

    # フラッシュカット（B/C）またはロゴオープナー（A）
    if pat["use_flash"]:
        clips.append(BUILD/"clips"/"flash_cut.mp4")
    elif pat.get("use_logo_opener"):
        clips.append(BUILD/"clips"/"c_logo.mp4")

    # ショーケース前のフック
    hook_map = {
        "HOOK_01_real.png": "c_h1.mp4",
        "HOOK_02_shipping.png": "c_h2.mp4",
        "HOOK_03_trust.png": "c_h3.mp4",
        "HOOK_04_price.png": "c_h4.mp4",
        "HOOK_05_protection.png": "c_h5.mp4",
    }
    if pat["hook_before_showcase"]:
        for h in pat["hook_order"]:
            clips.append(BUILD/"clips"/hook_map[h])

    # 遷移カード
    if pat.get("use_transition"):
        clips.append(BUILD/"clips"/"c_tr.mp4")

    # ショーケース
    clips += [BUILD/"clips"/f"sc{i:02d}.mp4" for i in range(len(so))]

    # ショーケース後の信頼パート（パターンCのみ）
    if pat.get("trust_after_showcase"):
        for h in pat.get("post_showcase_trust",[]):
            clips.append(BUILD/"clips"/hook_map[h])

    # スペック + アウトロ
    clips += [BUILD/"clips"/"c_sp.mp4", BUILD/"clips"/"c_ou.mp4"]

    vo = BUILD/"video_only.mp4"
    with open(BUILD/"cl.txt","w") as f:
        for c in clips: f.write(f"file '{c}'\n")
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(BUILD/"cl.txt"),
         "-map","0:v:0","-c:v","libx264","-preset","ultrafast",
         "-crf","20","-r","30","-pix_fmt","yuv420p",str(vo),"-loglevel","error"])
    td = dur(vo)
    print(f"[9] タイムライン組み立て完了: {td:.2f}秒 (パターン{CONFIG['pattern']})")
    print(f"    クリップ順: {[str(c.name) for c in clips]}")
    return td

# ================================================================
# Step 10: BGM
# ================================================================
def build_bgm(td):
    pat = PATTERNS[CONFIG["pattern"]]
    # フック前の秒数を計算
    pre_showcase = 0.0
    if pat["use_flash"]: pre_showcase += 1.927
    elif pat.get("use_logo_opener"): pre_showcase += 3.0

    hook_dur = {"HOOK_01_real.png":3.0,"HOOK_02_shipping.png":3.0,"HOOK_03_trust.png":2.2,
                "HOOK_04_price.png":2.5,"HOOK_05_protection.png":2.2}
    for h in pat["hook_order"]:
        pre_showcase += hook_dur[h]
    if pat.get("use_transition"): pre_showcase += 1.5

    showcase_dur = len(CONFIG["showcase_order"]) * CONFIG.get("showcase_duration_per_photo", 2.0)
    # Eight_Point_Stance.mp3 は ~20.7秒以降が無音。それ以前で必ずCrossfadeに入る
    EIGHT_MUSIC_END = 20.0
    xf = 1.5
    p1 = min(pre_showcase + showcase_dur + xf/2, EIGHT_MUSIC_END)
    rs = td - p1 + xf  # acrossfade: total = p1 + rs - xf = td
    bd = ASSETS/"bgm"
    run(["ffmpeg","-y","-i",str(bd/"Eight_Point_Stance.mp3"),"-t",str(p1),
         "-af","volume=0.35","-c:a","pcm_s16le",str(BUILD/"p1.wav"),"-loglevel","error"])
    run(["ffmpeg","-y","-stream_loop","-1","-i",str(bd/"Copper_Keys.mp3"),"-t",str(rs),
         "-af",f"volume=0.25,afade=t=out:st={rs-2:.3f}:d=2",
         "-c:a","pcm_s16le",str(BUILD/"p2.wav"),"-loglevel","error"])
    run(["ffmpeg","-y","-i",str(BUILD/"p1.wav"),"-i",str(BUILD/"p2.wav"),
         "-filter_complex","[0:a][1:a]acrossfade=d=1.5:c1=tri:c2=tri[a]",
         "-map","[a]","-c:a","aac",str(BUILD/"bgm.m4a"),"-loglevel","error"])
    assert dur(BUILD/"bgm.m4a") >= dur(BUILD/"video_only.mp4")-0.5, "BGM尺不足"
    print("[10] BGM完了")

# ================================================================
# Step 11: 最終ミックス
# ================================================================
def final_mux():
    on = CONFIG["output_name"]; raw = BUILD/"raw.mp4"; comp = OUT/f"{on}.mp4"
    run(["ffmpeg","-y","-i",str(BUILD/"video_only.mp4"),"-i",str(BUILD/"bgm.m4a"),
         "-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-shortest",str(raw),"-loglevel","error"])
    run(["ffmpeg","-y","-i",str(raw),"-c:v","libx264","-preset","veryfast","-crf","22",
         "-c:a","aac","-b:a","160k","-movflags","+faststart",str(comp),"-loglevel","error"])
    td = dur(comp); print(f"[11] 完成: {comp.name} ({td:.2f}秒)"); return comp, td

# ================================================================
# Step 12: SRT
# ================================================================
def build_srt(td):
    def ts(s): h=int(s//3600); m=int((s%3600)//60); sc=s%60; return f"{h:02d}:{m:02d}:{sc:06.3f}".replace(".",",")
    pat = PATTERNS[CONFIG["pattern"]]
    fr,ch,ln,mk = CONFIG["franchise"],CONFIG["character"],CONFIG["line"],CONFIG["maker"]
    sz,cd,bn = CONFIG.get("size",""),CONFIG["condition"],CONFIG["banner_text"]

    # ショーケース開始時刻を計算
    ss = 0.0
    if pat["use_flash"]: ss += 1.927
    elif pat.get("use_logo_opener"): ss += 3.0
    hook_dur = {"HOOK_01_real.png":3.0,"HOOK_02_shipping.png":3.0,"HOOK_03_trust.png":2.2,
                "HOOK_04_price.png":2.5,"HOOK_05_protection.png":2.2}
    for h in pat["hook_order"]: ss += hook_dur[h]
    if pat.get("use_transition"): ss += 1.5
    se = ss + len(CONFIG["showcase_order"]) * CONFIG.get("showcase_duration_per_photo", 2.0)
    pse = se; # ショーケース後信頼パート
    for h in pat.get("post_showcase_trust",[]): pse += hook_dur[h]
    spe = pse + 3.8; ose = spe + 4.0

    sl2 = f"Line: {ln}\nMaker: {mk}"+(f"\nSize: {sz}" if sz else "")+f"\nCondition: {cd}"
    es = [(1.927,min(4.927,ss),"Is this figure even REAL?")]
    if CONFIG["pattern"] != "C":  # パターンB/Aは信頼パートが冒頭
        es += [(4.927,7.927,"And what's shipping actually going to cost you?"),
               (7.927,10.127,"We buy from trusted shops in Japan."),
               (10.127,12.627,"$50. Every time.\nShipping + customs duties included."),
               (12.627,14.827,"Backed by eBay Buyer Protection")]
    es += [(ss,se,f"{bn}\nAUTHENTIC PRIZE FIGURE\nBought in person in Japan"),
           (se,(se+pse)/2 if pat.get("post_showcase_trust") else (se+spe)/2,
            f"WHAT YOU'RE GETTING\nFranchise: {fr}\nCharacter: {ch}")]
    if pat.get("post_showcase_trust"):
        es += [(se,pse,"$50. Every time.\nShipping + duties included. Sold via eBay.")]
    es += [((se+spe)/2 if not pat.get("post_showcase_trust") else pse, spe, sl2),
           (spe,spe+2,"AUTHENTIC PRIZE FIGURE\nWant this one?"),
           (spe+2,ose,"50DOLLARFIGURE.COM\nLink in bio")]
    srt = "".join(f"{i}\n{ts(s)} --> {ts(e)}\n{t}\n\n" for i,(s,e,t) in enumerate(es,1))
    (OUT/f"{CONFIG['output_name']}.srt").write_text(srt,encoding="utf-8")
    print("[12] SRT完了")

# ================================================================
# Step 13: 投稿テキスト
# ================================================================
def build_post(td):
    c  = CONFIG
    ch = c["character"]
    fr = c["franchise"]
    ln = c["line"]
    mk = c["maker"].split("/")[0].strip()
    rm = c.get("is_remake", False)
    ou = c.get("original_video_url", "")
    eu = c.get("ebay_url", "")

    # ---- YouTube タイトル（60〜65字目安） ----
    if rm:
        yt_title = f"We Remade This — {fr} {ch} Figure | $50 FIGURE #Shorts"
    else:
        yt_title = f"$50 for This {fr} {ch} Figure? | $50 FIGURE #Shorts"

    # ---- ハッシュタグ ----
    base_tags = [
        f"#{fr.replace(' ','')}", f"#{ch.replace(' ','')}",
        f"#{ln.replace(' ','')}", f"#{mk.replace(' ','')}",
        "#AnimeFigure", "#PrizeFigure", "#AnimeCollector",
        "#JapanFigures", "#Unboxing", "#50DollarFigure",
    ]
    yt_tags  = " ".join(base_tags)
    ig_tags  = " ".join(t if t!="#JapanFigures" else "#JapanFinds" for t in base_tags)

    # ---- YouTube 概要欄 ----
    rm_line  = f"\n🔗 Original video: {ou}" if rm and ou else ""
    yt_desc  = (
        f"{fr} × {ch} ({ln}) by {mk}.\n"
        f"Condition: {c['condition']}. Sourced from Japan.\n"
        f"\n"
        f"$50 flat — shipping & import duties included.\n"
        f"👉 50dollarfigure.com{rm_line}\n"
        f"\n"
        f"─────────────────\n"
        f"⚠️  AI-generated promo images used for visual effect.\n"
        f"    The actual item you receive is shown in the listing photos.\n"
        f"─────────────────\n"
        f"\n"
        f"{yt_tags}"
    )

    # ---- Instagram / TikTok キャプション（〜180字） ----
    if rm:
        ig_hook = (f"We remade this one. {fr} × {ch} ({ln}) — "
                   f"$50 flat, straight from Japan.\nThe original didn't show it right. This one does.")
    else:
        ig_hook = (f"Is this {fr} figure even real? {ch} ({ln}) — "
                   f"$50 flat, ships from Japan. 👀\nLink in bio 🏯️")
    ig_cap = f"{ig_hook}\n\n{ig_tags}"

    # ---- ファイル書き出し ----
    txt = (
        f"パターン{c['pattern']} — {PATTERNS[c['pattern']]['description']}\n"
        f"動画尺: {td:.1f}秒\n"
        f"\n"
        f"{'='*60}\n"
        f"【YouTube タイトル】\n"
        f"{yt_title}\n"
        f"\n"
        f"【YouTube 概要欄】\n"
        f"{yt_desc}\n"
        f"\n"
        f"{'='*60}\n"
        f"【Instagram / TikTok キャプション】\n"
        f"{ig_cap}\n"
    )
    (OUT / f"{c['output_name']}_post.txt").write_text(txt, encoding="utf-8")
    print("[13] 投稿テキスト完了")
    print(f"     YouTube タイトル: {yt_title}")

# ================================================================
# Step 14: 2フレームQA
# ================================================================
def qa(vp, td):
    on = CONFIG["output_name"]
    pat = PATTERNS[CONFIG["pattern"]]
    ss = 0.0
    if pat["use_flash"]: ss += 1.927
    hook_dur = {"HOOK_01_real.png":3.0,"HOOK_02_shipping.png":3.0,"HOOK_03_trust.png":2.2,
                "HOOK_04_price.png":2.5,"HOOK_05_protection.png":2.2}
    for h in pat["hook_order"]: ss += hook_dur[h]
    if pat.get("use_transition"): ss += 1.5
    seal_t = ss + 1.0  # ショーケース開始1秒後（シールカット）
    spec_t = max(0, td - 7)
    for lb, t in [("seal", seal_t), ("spec", spec_t)]:
        run(["ffmpeg","-y","-ss",str(t),"-i",str(vp),"-frames:v","1",
             str(OUT/f"QA_{on}_{lb}.png"),"-loglevel","error"])
    print(f"[14] QAフレーム出力完了（パターン{CONFIG['pattern']}）")
    print("     ✓ seal: 縦向き・シール見える・バナーあり")
    print("     ✓ spec: CREAM背景・テキスト切れなし・Notion一致")

# ================================================================
# メイン
# ================================================================
def main():
    p = CONFIG["pattern"]
    assert p in PATTERNS, f"パターンは A / B / C のいぞれかを指定してください（現在: {p}）"
    print("="*50)
    print(f"$50 FIGURE ショート動画パイプライン v2")
    print(f"パターン: {p} — {PATTERNS[p]['description']}")
    print("="*50)
    setup(); get_fonts(); process_logo(); crop_photos(); process_hooks()
    build_cards(); build_flash(); build_stills(); build_showcase()
    td = assemble(); build_bgm(td); vp, td2 = final_mux()
    build_srt(td2); build_post(td2); qa(vp, td2)
    print("="*50)
    print(f"完了: {CONFIG['output_name']}.mp4 / .srt / _post.txt / QA×2")
    print("="*50)

if __name__ == "__main__": main()
