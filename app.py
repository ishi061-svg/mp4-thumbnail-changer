"""MP4サムネイル変更：画面の係。ffmpeg の処理は core.py にまかせる。

動かし方：python app.py
必要なもの：ffmpeg、tkinterdnd2（ドラッグ＆ドロップ用。入れ方：python -m pip install tkinterdnd2）
"""
import os
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD  # ドラッグ＆ドロップを受け取るための追加の部品

import core

VIDEO_EXTS = (".mp4",)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

PREVIEW_W, PREVIEW_H = 320, 180
MSG_NO_VIDEO = "MP4 を「追加」してください"
MSG_NO_IMAGE = "サムネイルにする画像を選んでください"
MSG_READY = "準備ができました。「サムネイルを変える」を押してください"


def fmt_time(sec):
    """秒を「1:23.4」の形にする。"""
    m, s = divmod(max(0.0, sec), 60)
    return f"{int(m)}:{s:04.1f}"


def parse_time(text):
    """「1:23.4」「83.4」「83」などを秒にする。読めなければ None。"""
    text = text.strip().replace("：", ":")
    try:
        if ":" in text:
            m, s = text.split(":", 1)
            sec = int(m) * 60 + float(s)
        else:
            sec = float(text)
    except ValueError:
        return None
    if sec != sec or sec < 0:  # nan（数字でない値）やマイナスをはじく
        return None
    return sec


class App(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title("MP4サムネイル変更")
        self.resizable(False, False)
        self.videos = {}                            # 動画の場所 → 長さ（秒）。入れた順に並ぶ
        self.image_path = tk.StringVar()
        self.source = tk.StringVar(value="image")   # image=画像ファイル / scene=動画の場面
        self.save_mode = tk.StringVar(value="new")  # new=別ファイル / overwrite=上書き
        self.time_text = tk.StringVar(value="0:00.0")
        self.status = tk.StringVar(value=MSG_NO_VIDEO)
        self.scene_sec = 0.0
        self.current = None          # 場面の見本に使っている動画
        self._image_preview = None   # 画像モードの見本（消されないように持っておく）
        self._scene_preview = None   # 場面モードの見本
        self._preview_job = None     # スライダーを動かしている間、見本づくりを待たせる係
        self._preview_req = 0        # いちばん新しい見本の番号（古い結果で上書きしないため）
        self._busy = False
        self._tmpdir = tempfile.mkdtemp(prefix="mp4thumb_")
        self._build()
        self._register_drop(self)
        self._switch_source()

    # ---------- ドラッグ＆ドロップ ----------
    def _register_drop(self, widget):
        """widget とその中の部品すべてで、ドラッグ＆ドロップを受け取れるようにする"""
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", self._on_drop)
        for child in widget.winfo_children():
            self._register_drop(child)

    def _on_drop(self, event):
        """落とされたものを、動画・画像・フォルダに分けて受け取る"""
        if self._busy:
            return  # 処理中は、一覧が途中で変わらないように受け付けない
        # 落とされたものの一覧は1つの文字列で届く。空白を含む名前は { } で囲まれているので、splitlist で分ける
        videos, images, rejected, made = [], [], 0, 0
        for p in self.tk.splitlist(event.data):
            p = os.path.normpath(p)
            ext = os.path.splitext(p)[1].lower()
            if os.path.isdir(p):
                # フォルダは、すぐ下にある MP4 を全部入れる。
                # ただし、このアプリが作った「_thumb.mp4」は外す（_thumb_thumb ができないように）
                for n in sorted(os.listdir(p)):
                    full = os.path.join(p, n)
                    if not (n.lower().endswith(VIDEO_EXTS) and os.path.isfile(full)):
                        continue
                    if os.path.splitext(n)[0].endswith("_thumb"):
                        made += 1
                    else:
                        videos.append(full)
            elif ext in VIDEO_EXTS:
                videos.append(p)
            elif ext in IMAGE_EXTS:
                images.append(p)
            else:
                rejected += 1

        parts = []
        if videos:
            before = len(self.videos)
            self.add_videos(videos)
            parts.append(f"動画を {len(self.videos) - before} 本入れました（全部で {len(self.videos)} 本）")
        if images:
            if self.set_image(images[0]):
                self.source.set("image")
                self._switch_source()
                parts.append(f"画像「{os.path.basename(images[0])}」を使います")
                if len(images) > 1:
                    parts.append("画像は最初の1枚だけ使います")
        if made:
            parts.append(f"このアプリで作った「_thumb.mp4」{made} 本は入れませんでした")
        if rejected:
            parts.append(f"MP4・画像ではないものが {rejected} 個あったので、入れませんでした")
        if parts:
            self.status.set("。".join(parts))
        return event.action

    # ---------- 画面づくり ----------
    def _build(self):
        pad = {"padx": 10, "pady": 5}
        f = ttk.Frame(self, padding=10)
        f.grid()

        # 動画の一覧
        lf = ttk.LabelFrame(f, text="動画（MP4）", padding=5)
        lf.grid(row=0, column=0, columnspan=3, sticky="we", **pad)
        self.tree = ttk.Treeview(lf, columns=("name", "result"), show="headings", height=5, selectmode="browse")
        self.tree.heading("name", text="ファイル名")
        self.tree.heading("result", text="結果")
        self.tree.column("name", width=300)
        self.tree.column("result", width=230)
        self.tree.grid(row=0, column=0, columnspan=3)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=3, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.tag_configure("ok", foreground="#1a7f37")
        self.tree.tag_configure("skip", foreground="#9a6700")
        self.tree.tag_configure("error", foreground="#cf222e")
        self.add_btn = ttk.Button(lf, text="追加…", command=self._choose_videos)
        self.add_btn.grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.remove_btn = ttk.Button(lf, text="一覧から外す", command=self._remove_selected)
        self.remove_btn.grid(row=1, column=1, pady=(5, 0))
        self.clear_btn = ttk.Button(lf, text="全部外す", command=self._clear_videos)
        self.clear_btn.grid(row=1, column=2, sticky="e", pady=(5, 0))

        # サムネイルにするもの：タブで切りかえる（どちらの使い方もあると、ひと目で分かるように）
        ttk.Label(f, text="サムネイルにするもの（一覧の全部の動画に使います）").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(5, 0))
        self.tabs = ttk.Notebook(f)
        self.tabs.grid(row=2, column=0, columnspan=3, sticky="we", **pad)

        # 画像ファイルのタブ
        self.image_row = ttk.Frame(self.tabs, padding=5)
        self.tabs.add(self.image_row, text="　画像ファイル　")
        ttk.Label(self.image_row, text="サムネイル画像").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(self.image_row, textvariable=self.image_path, width=52, state="readonly").grid(row=0, column=1, **pad)
        ttk.Button(self.image_row, text="選ぶ…", command=self._choose_image).grid(row=0, column=2, **pad)

        # 動画の場面のタブ（スライダーと時間の入力欄）
        self.scene_row = ttk.Frame(self.tabs, padding=5)
        self.tabs.add(self.scene_row, text="　動画の場面（スライダー・時間指定）　")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self._on_tab_changed())
        ttk.Label(self.scene_row, text="場面の時間").grid(row=0, column=0, sticky="w", **pad)
        self.slider = ttk.Scale(self.scene_row, from_=0, to=1, length=340, command=self._on_slider)
        self.slider.grid(row=0, column=1, **pad)
        self.time_entry = ttk.Entry(self.scene_row, textvariable=self.time_text, width=9, justify="center")
        self.time_entry.grid(row=0, column=2, padx=(10, 2))
        self.time_entry.bind("<Return>", lambda e: self._on_time_entry())
        ttk.Button(self.scene_row, text="表示", width=5, command=self._on_time_entry).grid(row=0, column=3)
        self.length_label = ttk.Label(self.scene_row, text="（動画を追加すると動かせます）", foreground="#666")
        self.length_label.grid(row=1, column=1, sticky="w", padx=10)

        # 見本の絵を出す場所（大きさを固定して、絵がなくても画面がガタつかないように）
        box = tk.Frame(f, width=PREVIEW_W, height=PREVIEW_H, bg="#ddd")
        box.grid(row=3, column=0, columnspan=3, **pad)
        box.pack_propagate(False)
        self.preview = tk.Label(box, bg="#ddd")
        self.preview.pack(expand=True, fill="both")

        mode = ttk.LabelFrame(f, text="保存のしかた", padding=5)
        mode.grid(row=4, column=0, columnspan=3, sticky="we", **pad)
        ttk.Radiobutton(mode, text="別ファイルで保存（元の動画は残す。「名前_thumb.mp4」ができます）",
                        variable=self.save_mode, value="new").pack(anchor="w")
        ttk.Radiobutton(mode, text="上書き（元の動画そのものを変える）",
                        variable=self.save_mode, value="overwrite").pack(anchor="w")

        self.run_btn = ttk.Button(f, text="サムネイルを変える", command=self._run)
        self.run_btn.grid(row=5, column=0, columnspan=3, **pad)
        ttk.Label(f, textvariable=self.status, foreground="#333").grid(row=6, column=0, columnspan=3, **pad)

    def _switch_source(self):
        """「画像ファイル」と「動画の場面」で、開くタブと見本を切りかえる。"""
        if self.source.get() == "image":
            tab = self.image_row
            self._show_preview(self._image_preview, "（画像の見本）")
        else:
            tab = self.scene_row
            self._show_preview(self._scene_preview, "（場面の見本）")
        if self.tabs.select() != str(tab):
            self.tabs.select(tab)
        self._update_button()

    def _on_tab_changed(self):
        """タブがクリックされたら、使うもの（画像 / 場面）をそれに合わせる。"""
        source = "image" if self.tabs.select() == str(self.image_row) else "scene"
        if source != self.source.get():
            self.source.set(source)
            self._switch_source()

    def _show_preview(self, img, empty_text):
        if img:
            self.preview.configure(image=img, text="")
        else:
            self.preview.configure(image="", text=empty_text)

    # ---------- 動画の一覧 ----------
    def _choose_videos(self):
        paths = filedialog.askopenfilenames(title="MP4 を選ぶ（何本でも）",
                                            filetypes=[("MP4 動画", "*.mp4"), ("すべて", "*.*")])
        if paths:
            self.add_videos([os.path.normpath(p) for p in paths])

    def add_videos(self, paths):
        bad = []
        for path in paths:
            if path in self.videos:
                continue  # 同じ動画は二重に入れない
            try:
                duration = core.get_duration(path)
            except Exception:
                bad.append(os.path.basename(path))
                continue
            self.videos[path] = duration
            self.tree.insert("", "end", iid=path, values=(os.path.basename(path), ""))
        if bad:
            messagebox.showwarning("読めない動画がありました",
                                   "次の動画は読み込めなかったので、一覧に入れませんでした。\n\n" + "\n".join(bad))
        # まだ何も選んでいなければ、最初の動画を見本に使う
        if not self.tree.selection() and self.videos:
            self.tree.selection_set(next(iter(self.videos)))
        self._update_button()

    def _remove_selected(self):
        for iid in self.tree.selection():
            self.tree.delete(iid)
            self.videos.pop(iid, None)
        if self.videos:
            self.tree.selection_set(next(iter(self.videos)))
        else:
            self._set_current(None)
        self._update_button()

    def _clear_videos(self):
        self.tree.delete(*self.tree.get_children())
        self.videos.clear()
        self._set_current(None)
        self._update_button()

    def _on_select(self):
        sel = self.tree.selection()
        if sel and sel[0] != self.current:
            self._set_current(sel[0])

    def _set_current(self, path):
        """場面の見本に使う動画を変える。スライダーの長さもその動画に合わせる。"""
        self.current = path
        self._scene_preview = None
        if path is None:
            self.slider.configure(to=1)
            self.length_label.configure(text="（動画を追加すると動かせます）")
            self.slider.set(0)
            self.time_text.set(fmt_time(0))
            self.scene_sec = 0.0
            if self.source.get() == "scene":
                self._show_preview(None, "（場面の見本）")
            return
        duration = self.videos[path]
        # 最後の瞬間は場面が取れないことがあるので、少し手前までにする
        self.slider.configure(to=max(0.0, duration - 0.1))
        self.length_label.configure(
            text=f"見本：{os.path.basename(path)}（長さ {fmt_time(duration)}）　入力例：1:23.4 / 83.4")
        self.set_scene(self.scene_sec)

    # ---------- 画像を選ぶ ----------
    def _choose_image(self):
        path = filedialog.askopenfilename(
            title="サムネイルにする画像を選ぶ",
            filetypes=[("画像", "*.jpg *.jpeg *.png *.bmp *.webp"), ("すべて", "*.*")])
        if path and self.set_image(os.path.normpath(path)):
            self._switch_source()

    def set_image(self, path):
        """サムネイルにする画像を決めて、見本を作る。読めなければ False。"""
        png = os.path.join(self._tmpdir, "image_preview.png")
        try:
            core.make_preview(path, png, PREVIEW_W, PREVIEW_H)
            self._image_preview = tk.PhotoImage(file=png)
        except Exception:
            messagebox.showerror("読めません", f"この画像は読み込めませんでした。\n別の画像を選んでください。\n\n{os.path.basename(path)}")
            return False
        self.image_path.set(path)
        return True

    # ---------- 場面を選ぶ ----------
    def _on_slider(self, value):
        self.scene_sec = float(value)
        self.time_text.set(fmt_time(self.scene_sec))
        # 動かしている間に毎回 ffmpeg を動かすと重いので、手が止まって 0.2 秒後に見本を作る
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(200, self._request_scene_preview)

    def _on_time_entry(self):
        if not self.current:
            return
        sec = parse_time(self.time_text.get())
        if sec is None:
            messagebox.showwarning("時間が読めません", "時間は「1:23.4」か「83.4」のように入れてください。")
            self.time_text.set(fmt_time(self.scene_sec))
            return
        if sec > self.videos[self.current]:
            messagebox.showwarning("長さを超えています",
                                   f"見本の動画の長さは {fmt_time(self.videos[self.current])} です。")
            self.time_text.set(fmt_time(self.scene_sec))
            return
        self.set_scene(sec)

    def set_scene(self, sec):
        sec = min(sec, float(self.slider.cget("to")))
        self.scene_sec = sec
        self.slider.set(sec)
        self.time_text.set(fmt_time(sec))
        self._request_scene_preview()

    def _request_scene_preview(self):
        self._preview_job = None
        if not self.current:
            return
        self._preview_req += 1
        req = self._preview_req
        video, sec = self.current, self.scene_sec
        png = os.path.join(self._tmpdir, f"scene_{req}.png")

        def work():
            try:
                core.extract_frame(video, sec, png, PREVIEW_W, PREVIEW_H)
                ok = True
            except Exception:
                ok = False
            self.after(0, lambda: self._scene_preview_ready(req, png, ok))
        # 別の流れ（スレッド）で作り、スライダーの動きが固まらないようにする
        threading.Thread(target=work, daemon=True).start()

    def _scene_preview_ready(self, req, png, ok):
        if req != self._preview_req:   # もっと新しい見本を頼んでいたら、古い結果は使わない
            return
        if ok:
            self._scene_preview = tk.PhotoImage(file=png)
        if self.source.get() == "scene":
            self._show_preview(self._scene_preview, "（この時間の場面は表示できません）")

    # ---------- 実行 ----------
    def _ready(self):
        if not self.videos:
            return False
        return self.source.get() == "scene" or bool(self.image_path.get())

    def _update_button(self):
        state = "normal" if not self._busy else "disabled"
        for b in (self.add_btn, self.remove_btn, self.clear_btn):
            b.configure(state=state)
        self.run_btn.configure(state="normal" if self._ready() and not self._busy else "disabled")
        if self._busy:
            return
        if not self.videos:
            self.status.set(MSG_NO_VIDEO)
        elif not self._ready():
            self.status.set(MSG_NO_IMAGE)
        elif self.status.get() in (MSG_NO_VIDEO, MSG_NO_IMAGE):
            self.status.set(MSG_READY)

    def _run(self):
        videos = list(self.videos)
        overwrite = self.save_mode.get() == "overwrite"
        if overwrite:
            if not messagebox.askyesno(
                    "上書きの確認", f"一覧の {len(videos)} 本の動画を上書きします。元には戻せません。\n\nよろしいですか？"):
                return
        else:
            exist = [os.path.basename(core.thumb_output_path(v)) for v in videos
                     if os.path.exists(core.thumb_output_path(v))]
            if exist and not messagebox.askyesno(
                    "同じ名前のファイルがあります",
                    f"次の {len(exist)} 個のファイルはすでにあります。上書きしますか？\n\n" + "\n".join(exist[:10])
                    + ("\n…" if len(exist) > 10 else "")):
                return

        if self.source.get() == "image":
            job = ("image", self.image_path.get())
        else:
            job = ("scene", self.scene_sec)
        for v in videos:
            self.tree.item(v, values=(os.path.basename(v), "待っています"), tags=())
        self._busy = True
        self._update_button()
        # 別の流れ（スレッド）で動かし、何本あっても画面が固まらないようにする
        threading.Thread(target=self._work, args=(videos, job, overwrite), daemon=True).start()

    def _work(self, videos, job, overwrite):
        kind, value = job
        counts = {"ok": 0, "skip": 0, "error": 0}
        for i, video in enumerate(videos, 1):
            self.after(0, lambda i=i: self.status.set(f"処理しています…（{i} / {len(videos)}）"))
            self.after(0, lambda v=video: self._set_result(v, "処理中…", ()))
            try:
                if not os.path.exists(video):
                    raise RuntimeError("ファイルが見つかりません")
                if kind == "image":
                    image = value
                else:
                    if value > self.videos.get(video, 0) - 0.05:
                        tag, text = "skip", f"スキップ：動画が短い（{fmt_time(self.videos[video])}）"
                        counts[tag] += 1
                        self.after(0, lambda v=video, t=text: self._set_result(v, t, ("skip",)))
                        continue
                    # 場面は元の大きさのまま JPEG にして、それをカバー画像にする
                    image = os.path.join(self._tmpdir, "scene_cover.jpg")
                    core.extract_frame(video, value, image)
                out = core.set_cover(video, image, overwrite)
                tag, text = "ok", f"できました → {os.path.basename(out)}"
            except Exception as e:
                # 1本が失敗しても止めず、その1本だけ「エラー」にして次へ進む
                tag, text = "error", "エラー：" + str(e).splitlines()[0][:60]
            counts[tag] += 1
            self.after(0, lambda v=video, t=text, g=tag: self._set_result(v, t, (g,)))
        msg = f"終わりました：できた {counts['ok']} 本"
        if counts["skip"]:
            msg += f"、スキップ {counts['skip']} 本"
        if counts["error"]:
            msg += f"、エラー {counts['error']} 本"
        self.after(0, lambda: self._done(msg))

    def _set_result(self, video, text, tags):
        if self.tree.exists(video):
            self.tree.item(video, values=(os.path.basename(video), text), tags=tags)

    def _done(self, msg):
        self._busy = False
        self.status.set(msg)
        self._update_button()


if __name__ == "__main__":
    App().mainloop()
