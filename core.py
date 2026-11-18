"""ffmpeg を動かす係。画面（app.py）からはここの関数だけを呼ぶ。"""
import os
import subprocess
import tempfile

# exe から ffmpeg を動かしたときに、黒い画面が一瞬出ないようにする
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd):
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg が失敗しました")
    return result.stdout


def thumb_output_path(video_path):
    """別ファイルで保存するときの名前：「名前_thumb.mp4」"""
    base, ext = os.path.splitext(video_path)
    return f"{base}_thumb{ext}"


def set_cover(video_path, image_path, overwrite=False):
    """MP4 にカバー画像を入れる。すでにカバー画像があれば入れ替える。

    overwrite=False … 「名前_thumb.mp4」を新しく作る（元は残す）
    overwrite=True  … 元のファイルを置きかえる
    戻り値：できたファイルの場所
    """
    final_path = video_path if overwrite else thumb_output_path(video_path)
    # まず同じフォルダの一時ファイルに書き、成功したときだけ置きかえる。
    # 途中で失敗しても、元のファイルが壊れないようにするため。
    folder = os.path.dirname(os.path.abspath(video_path))
    fd, tmp_path = tempfile.mkstemp(suffix=".mp4", dir=folder, prefix="~thumb_")
    os.close(fd)
    try:
        _run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", video_path,
            "-i", image_path,
            # 0:V は「カバー画像ではない本物の動画」。前のカバー画像は捨てて入れ替える
            "-map", "0:V:0", "-map", "0:a?", "-map", "1:0",
            "-c", "copy",                       # 元の動画・音声は作り直さない
            "-c:v:1", "mjpeg",                  # カバー画像は JPEG にする
            "-disposition:v:1", "attached_pic", # 「これは表紙です」という印
            tmp_path,
        ])
        os.replace(tmp_path, final_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return final_path


def get_duration(video_path):
    """動画の長さ（秒）を ffprobe で調べる。"""
    out = _run([
        "ffprobe", "-v", "error", "-select_streams", "V:0",
        "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", video_path,
    ])
    try:
        return float(out.strip())
    except ValueError:
        raise RuntimeError("動画の長さが読み取れませんでした")


def extract_frame(video_path, seconds, out_path, width=None, height=None):
    """動画の「seconds 秒」の場面を1枚の絵にする。
    width/height を渡すと、その枠に収まる大きさに縮める（画面の見本用）。"""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.0, seconds):.3f}",   # -i の前に書くと、目的の時間へすばやく飛べる
        "-i", video_path,
        "-map", "0:V:0",                     # カバー画像ではなく本物の動画から取る
        "-frames:v", "1",
    ]
    if width and height:
        cmd += ["-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease"]
    if out_path.lower().endswith((".jpg", ".jpeg")):
        cmd += ["-q:v", "2"]                 # JPEG はきれいめの画質で保存
    msg = "その時間の場面を取り出せませんでした。動画の長さを超えていないか確かめてください"
    try:
        _run(cmd + [out_path])
    except RuntimeError:
        raise RuntimeError(msg) from None
    if not os.path.exists(out_path):
        raise RuntimeError(msg)
    return out_path


def make_preview(image_path, out_png, width=320, height=180):
    """画面に出す見本の絵（PNG）を作る。tkinter は JPEG をそのまま表示できないため。"""
    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", image_path,
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        "-frames:v", "1", out_png,
    ])
    return out_png
