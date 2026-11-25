"""MP4サムネイル変更：画面の係。ffmpeg の処理は core.py にまかせる。

動かし方：python app.py
必要なもの：ffmpeg、tkinterdnd2（ドラッグ＆ドロップ用。入れ方：python -m pip install tkinterdnd2）
"""
import os
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from tkinterdnd2 import DND_FILES, TkinterDnD  # ドラッグ＆ドロップを受け取るための追加の部品

import core

VIDEO_EXTS = (".mp4",)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

PREVIEW_W, PREVIEW_H = 320, 180

# ---- 色と文字（ここを変えると、画面全体の見た目が変わる） ----
BG = "#f3f5f4"           # 画面の背景（薄い灰色）
CARD = "#ffffff"         # カードの背景（白）
BORDER = "#dce3e1"       # カードや入力欄の枠
GREEN = "#14796f"        # 差し色（ポートフォリオの緑に合わせた）
GREEN_DARK = "#0f5f57"   # ボタンを押したときの緑
GREEN_LIGHT = "#e4f1ee"  # 表の見出し・ボタンに乗ったときの薄い緑
TEXT = "#1f2a30"         # ふつうの文字
SUB = "#6b7780"          # 補足の文字（薄い灰色）
FONT = "Yu Gothic UI"

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

    # ---------- 見た目の決まり ----------
    def _setup_style(self):
        """ttk（tkinter の部品）の色・文字・形を、アプリ全体でまとめて決める。"""
        self.configure(bg=BG)
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            tkfont.nametofont(name).configure(family=FONT, size=10)
        s = ttk.Style(self)
        s.theme_use("clam")  # 色を自由に変えやすい形
        s.configure(".", background=BG, foreground=TEXT, font=(FONT, 10),
                    bordercolor=BORDER, lightcolor=CARD, darkcolor=BORDER, focuscolor=GREEN)
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD)
        s.configure("TLabel", background=BG)
        s.configure("Card.TLabel", background=CARD)
        s.configure("CardTitle.TLabel", background=CARD, font=(FONT, 12, "bold"))
        s.configure("Sub.TLabel", background=CARD, foreground=SUB, font=(FONT, 9))
        s.configure("Status.TLabel", background=BG, foreground=SUB, font=(FONT, 10))

        # ふつうのボタン：白地に枠線
        s.configure("TButton", background=CARD, foreground=TEXT, padding=(14, 5), relief="raised", width=-4,
                    borderwidth=1, bordercolor="#c3ccca", lightcolor=CARD, darkcolor=CARD)
        s.map("TButton", background=[("disabled", BG), ("pressed", GREEN_LIGHT), ("active", GREEN_LIGHT)],
              foreground=[("disabled", "#a9b3b8")], bordercolor=[("active", GREEN)])
        # 実行ボタン：大きな緑
        s.configure("Accent.TButton", background=GREEN, foreground="#ffffff", font=(FONT, 12, "bold"),
                    padding=(30, 10), bordercolor=GREEN, lightcolor=GREEN, darkcolor=GREEN)
        s.map("Accent.TButton",
              background=[("disabled", "#c5cfcd"), ("pressed", GREEN_DARK), ("active", GREEN_DARK)],
              foreground=[("disabled", "#ffffff")],
              bordercolor=[("disabled", "#c5cfcd"), ("active", GREEN_DARK)],
              lightcolor=[("disabled", "#c5cfcd"), ("active", GREEN_DARK)],
              darkcolor=[("disabled", "#c5cfcd"), ("active", GREEN_DARK)])

        # 一覧の表：行を広く、見出しは薄い緑
        s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                    rowheight=28, bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD)
        s.map("Treeview", background=[("selected", GREEN_LIGHT)], foreground=[("selected", TEXT)])
        s.configure("Treeview.Heading", background=GREEN_LIGHT, foreground=GREEN_DARK,
                    font=(FONT, 10, "bold"), relief="flat", padding=(6, 5), bordercolor=GREEN_LIGHT)
        s.map("Treeview.Heading", background=[("active", GREEN_LIGHT)])

        # タブ：選んでいるタブは白地に緑の太字＋上に緑の線
        s.configure("TNotebook", background=CARD, bordercolor=BORDER, tabmargins=(0, 0, 0, 0))
        s.configure("TNotebook.Tab", background=BG, foreground=SUB, padding=(16, 7),
                    bordercolor=BORDER, lightcolor=BG, font=(FONT, 10))
        s.map("TNotebook.Tab",
              background=[("selected", CARD)], foreground=[("selected", GREEN)],
              lightcolor=[("selected", GREEN)], font=[("selected", (FONT, 10, "bold"))],
              expand=[("selected", (0, 2, 0, 0))])

        # 入力欄・丸ボタン・スライダー
        s.configure("TEntry", fieldbackground=CARD, bordercolor=BORDER, lightcolor=CARD, padding=(6, 4))
        s.map("TEntry", bordercolor=[("focus", GREEN)], fieldbackground=[("readonly", "#f7f9f8")])
        s.configure("TRadiobutton", background=CARD, foreground=TEXT, indicatorbackground=CARD,
                    indicatorforeground=GREEN, indicatormargin=(0, 0, 8, 0), padding=(0, 3))
        s.map("TRadiobutton", background=[("active", CARD)], indicatorbackground=[("selected", CARD)])
        s.configure("Horizontal.TScale", background=GREEN, troughcolor="#e3eae8", bordercolor="#e3eae8",
                    lightcolor=GREEN, darkcolor=GREEN, sliderthickness=12, sliderlength=18, gripcount=0)
        s.map("Horizontal.TScale", background=[("active", GREEN_DARK)])
        for name in ("TScrollbar", "Vertical.TScrollbar"):
            s.configure(name, background="#d6dfdd", troughcolor=CARD, bordercolor=CARD,
                        arrowcolor=SUB, lightcolor="#d6dfdd", darkcolor="#d6dfdd", gripcount=0, arrowsize=12)
            s.map(name, background=[("active", "#b9c6c3")])

    def _card(self, parent, row, title, hint=None):
        """白いカードを作り、見出しを付けて、中身を入れる枠を返す。"""
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=16, pady=12)
        card.grid(row=row, column=0, sticky="we", pady=(0, 12))
        head = ttk.Frame(card, style="Card.TFrame")
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text=title, style="CardTitle.TLabel").pack(side="left")
        if hint:
            ttk.Label(head, text=hint, style="Sub.TLabel").pack(side="left", padx=(10, 0), pady=(3, 0))
        body = ttk.Frame(card, style="Card.TFrame")
        body.pack(fill="x")
        return body

    # ---------- 画面づくり ----------
    def _build(self):
        self._setup_style()

        # 上の帯：アプリ名と一言
        header = tk.Frame(self, bg=GREEN, padx=20, pady=14)
        header.grid(row=0, column=0, sticky="we")
        tk.Label(header, text="MP4サムネイル変更", bg=GREEN, fg="#ffffff",
                 font=(FONT, 16, "bold")).pack(anchor="w")
        tk.Label(header, text="エクスプローラーで見える MP4 の絵を、好きな画像や場面に変えます",
                 bg=GREEN, fg="#d5ece8", font=(FONT, 10)).pack(anchor="w")

        f = ttk.Frame(self, padding=(16, 16, 16, 10))
        f.grid(row=1, column=0, sticky="we")
        f.columnconfigure(0, weight=1)

        # 1. 動画の一覧
        lf = self._card(f, 0, "1. 動画を入れる", "ドラッグ＆ドロップでも入れられます（フォルダも可）")
        self.tree = ttk.Treeview(lf, columns=("name", "result"), show="headings", height=5, selectmode="browse")
        self.tree.heading("name", text="ファイル名", anchor="w")
        self.tree.heading("result", text="結果", anchor="w")
        self.tree.column("name", width=300)
        self.tree.column("result", width=250)
        self.tree.grid(row=0, column=0, columnspan=3, sticky="we")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=3, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.tag_configure("ok", foreground="#1a7f37")
        self.tree.tag_configure("skip", foreground="#9a6700")
        self.tree.tag_configure("error", foreground="#cf222e")
        self.add_btn = ttk.Button(lf, text="＋ 追加…", command=self._choose_videos)
        self.add_btn.grid(row=1, column=0, sticky="w", pady=(10, 0))
        btns = ttk.Frame(lf, style="Card.TFrame")
        btns.grid(row=1, column=1, columnspan=3, sticky="e", pady=(10, 0))
        self.remove_btn = ttk.Button(btns, text="一覧から外す", command=self._remove_selected)
        self.remove_btn.pack(side="left", padx=(0, 6))
        self.clear_btn = ttk.Button(btns, text="全部外す", command=self._clear_videos)
        self.clear_btn.pack(side="left")
        lf.columnconfigure(1, weight=1)

        # 2. サムネイルにするもの：タブで切りかえる（どちらの使い方もあると、ひと目で分かるように）
        sf = self._card(f, 1, "2. サムネイルにするもの", "一覧の全部の動画に使います")
        self.tabs = ttk.Notebook(sf)
        self.tabs.pack(fill="x")

        # 画像ファイルのタブ
        self.image_row = ttk.Frame(self.tabs, style="Card.TFrame", padding=(4, 12, 4, 6))
        self.tabs.add(self.image_row, text="画像ファイル")
        ttk.Label(self.image_row, text="サムネイル画像", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Entry(self.image_row, textvariable=self.image_path, width=46, state="readonly").grid(row=0, column=1, sticky="we")
        ttk.Button(self.image_row, text="選ぶ…", command=self._choose_image).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(self.image_row, text="JPG・PNG・BMP・WebP（画像を画面に落としても選べます）",
                  style="Sub.TLabel").grid(row=1, column=1, sticky="w", pady=(4, 0))
        self.image_row.columnconfigure(1, weight=1)

        # 動画の場面のタブ（スライダーと時間の入力欄）
        self.scene_row = ttk.Frame(self.tabs, style="Card.TFrame", padding=(4, 12, 4, 6))
        self.tabs.add(self.scene_row, text="動画の場面（スライダー・時間指定）")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self._on_tab_changed())
        ttk.Label(self.scene_row, text="場面の時間", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.slider = ttk.Scale(self.scene_row, from_=0, to=1, length=330, command=self._on_slider)
        self.slider.grid(row=0, column=1, sticky="we")
        self.time_entry = ttk.Entry(self.scene_row, textvariable=self.time_text, width=8, justify="center")
        self.time_entry.grid(row=0, column=2, padx=(12, 6))
        self.time_entry.bind("<Return>", lambda e: self._on_time_entry())
        ttk.Button(self.scene_row, text="表示", command=self._on_time_entry).grid(row=0, column=3)
        self.length_label = ttk.Label(self.scene_row, text="動画を入れると動かせます", style="Sub.TLabel")
        self.length_label.grid(row=1, column=1, columnspan=3, sticky="w", pady=(4, 0))
        self.scene_row.columnconfigure(1, weight=1)

        # 見本の絵を出す場所（大きさを固定して、絵がなくても画面がガタつかないように）
        box = tk.Frame(sf, width=PREVIEW_W, height=PREVIEW_H, bg="#eef2f1",
                       highlightbackground=BORDER, highlightthickness=1)
        box.pack(pady=(12, 0))
        box.pack_propagate(False)
        self.preview = tk.Label(box, bg="#eef2f1", fg=SUB, font=(FONT, 10))
        self.preview.pack(expand=True, fill="both")

        # 3. 保存のしかた
        mode = self._card(f, 2, "3. 保存のしかた")
        ttk.Radiobutton(mode, text="別ファイルで保存（元の動画は残す。「名前_thumb.mp4」ができます）",
                        variable=self.save_mode, value="new").pack(anchor="w")
        ttk.Radiobutton(mode, text="上書き（元の動画そのものを変える）",
                        variable=self.save_mode, value="overwrite").pack(anchor="w")

        self.run_btn = ttk.Button(f, text="サムネイルを変える", style="Accent.TButton", command=self._run)
        self.run_btn.grid(row=3, column=0, sticky="we", pady=(2, 8))
        ttk.Label(f, textvariable=self.status, style="Status.TLabel").grid(row=4, column=0)

    def _switch_source(self):
        """「画像ファイル」と「動画の場面」で、開くタブと見本を切りかえる。"""
        if self.source.get() == "image":
            tab = self.image_row
            self._show_preview(self._image_preview, "画像を選ぶと、ここに見本が出ます")
        else:
            tab = self.scene_row
            self._show_preview(self._scene_preview, "動画を入れると、ここに場面が出ます")
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
            self.length_label.configure(text="動画を入れると動かせます")
            self.slider.set(0)
            self.time_text.set(fmt_time(0))
            self.scene_sec = 0.0
            if self.source.get() == "scene":
                self._show_preview(None, "動画を入れると、ここに場面が出ます")
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
            self._show_preview(self._scene_preview, "この時間の場面は表示できません")

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
