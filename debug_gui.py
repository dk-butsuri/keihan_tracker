import asyncio
import sys
import os
import math
from datetime import datetime
import tkinter as tk

# 外部ライブラリ
try:
    import customtkinter as ctk
except ImportError:
    print("Error: 'customtkinter' is required. Please install it via 'pip install customtkinter'")
    sys.exit(1)

# カレントディレクトリをパスに追加してライブラリを読み込めるようにする
sys.path.append(os.getcwd())

from keihan_tracker import KHTracker
from keihan_tracker.train.tracker import ActiveTrainData, TrainType, StationData
from keihan_tracker.train.position_calculation import calc_position

# --- 設定 ---
REFRESH_RATE = 5   # 秒 (APIへのアクセス間隔)
GUI_UPDATE_RATE = 0.1 # 秒 (GUIのアニメーション・描画更新間隔)

# デザイン設定
COLOR_BG = "#1e1e1e"       # ウィンドウ背景
COLOR_CANVAS = "#2b2b2b"   # マップ背景
COLOR_LINE = "#555555"     # 線路の色
COLOR_STATION = "#aaaaaa"  # 駅マーカーの色
COLOR_STATION_SELECTED = "#00ff00" # 選択時の駅色
COLOR_TEXT_MAIN = "#e0e0e0"
COLOR_TEXT_SUB = "#aaaaaa"
COLOR_ACCENT = "#1f6aa5"
COLOR_SELECT = "#00ffff"   # 選択時のハイライト色

# 列車種別ごとの色定義
TYPE_COLORS = {
    TrainType.LTD_EXP: "#ff0000",        # 特急 (赤)
    TrainType.RAPID_LTD_EXP: "#ff0080",  # 快速特急 (ピンク)
    TrainType.LINER: "#ffffff",          # ライナー (白)
    TrainType.RAPID_EXP: "#ff8c00",      # 快速急行 (オレンジ)
    TrainType.COMMUTER_RAPID_EXP: "#ff4500", # 通勤快急
    TrainType.EXPRESS: "#ffa500",        # 急行 (薄オレンジ)
    TrainType.MIDNIGHT_EXP: "#ffd700",   # 深夜急行
    TrainType.SUB_EXP: "#00bfff",        # 準急 (青)
    TrainType.COMMUTER_SUB_EXP: "#1e90ff", # 通勤準急
    TrainType.SEMI_EXP: "#00fa9a",       # 区間急行 (緑)
    TrainType.LOCAL: "#808080",          # 普通 (グレー)
    TrainType.EXTRA_TRAIN: "#ffff00"     # 臨時 (黄)
}

# 路線定義 (駅IDリスト)
LINES = {
    "MAIN": [1, 2, 3] + list(range(4, 43)),  # 淀屋橋(1) - 出町柳(42)
    "NAKANOSHIMA": [54, 53, 52, 51],         # 中之島(54) - なにわ橋(51)
    "KATANO": [21] + list(range(61, 68)),    # 枚方市(21) - 私市(67)
    "UJI": [28] + list(range(71, 78))        # 中書島(28) - 宇治(77)
}

LINE_MAPPING = {
    "京阪本線・鴨東線": "MAIN",
    "中之島線": "NAKANOSHIMA",
    "交野線": "KATANO",
    "宇治線": "UJI"
}

# 描画レイアウト設定 (x_startを増やしてラベル表示スペース確保)
LAYOUT_CONFIG = {
    "MAIN":        {"y": 200, "x_start": 200, "x_step": 60, "label": "京阪本線・鴨東線"},
    "NAKANOSHIMA": {"y": 350, "x_start": 200, "x_step": 60, "label": "中之島線"},
    "KATANO":      {"y": 500, "x_start": 200, "x_step": 60, "label": "交野線"},
    "UJI":         {"y": 650, "x_start": 200, "x_step": 60, "label": "宇治線"},
}

class TrainBlock:
    """描画された列車オブジェクトを管理するクラス"""
    def __init__(self, canvas: "ctk.CTkCanvas", train_data: ActiveTrainData, x, y, size=16):
        self.canvas = canvas
        self.wdf = train_data.wdfBlockNo
        self.size = size
        
        self.create_objects(train_data, x, y)
        self.current_x = x
        self.current_y = y

    def create_objects(self, train_data: ActiveTrainData, x, y):
        color = TYPE_COLORS.get(train_data.train_type, "#ffffff")
        outline = "white" if train_data.delay_minutes > 0 else "black"
        
        self.shape_id = self.canvas.create_oval(
            x - self.size/2, y - self.size/2, x + self.size/2, y + self.size/2,
            fill=color, outline=outline, width=2,
            tags=("train", f"train_{self.wdf}")
        )
        
        info_text = f"{train_data.train_number}"
        if train_data.delay_minutes > 0:
            info_text += f"\n+{train_data.delay_minutes}"
            
        text_y = y + self.size + 5
        self.text_id = self.canvas.create_text(
            x, text_y,
            text=info_text,
            fill="white", font=("Consolas", 8),
            anchor="n",
            tags=("train_text", f"train_{self.wdf}")
        )
        
        self.select_id = self.canvas.create_oval(
            x - self.size/2 - 4, y - self.size/2 - 4, x + self.size/2 + 4, y + self.size/2 + 4,
            outline=COLOR_SELECT, width=3, state="hidden",
            tags=("train_select", f"train_{self.wdf}")
        )
        self.canvas.tag_lower(self.shape_id, self.select_id)

    def update_pos(self, x, y, train_data: ActiveTrainData):
        self.current_x = x
        self.current_y = y
        r = self.size / 2
        self.canvas.coords(self.shape_id, x - r, y - r, x + r, y + r)
        self.canvas.coords(self.select_id, x - r - 4, y - r - 4, x + r + 4, y + r + 4)
        
        text_y = y + self.size + 5
        self.canvas.coords(self.text_id, x, text_y)
        
        info_text = f"{train_data.train_number}"
        if train_data.delay_minutes > 0:
            info_text += f"\n+{train_data.delay_minutes}"
        self.canvas.itemconfigure(self.text_id, text=info_text)
        
        outline = "white" if train_data.delay_minutes > 0 else "black"
        self.canvas.itemconfigure(self.shape_id, outline=outline)

    def set_selected(self, selected: bool):
        state = "normal" if selected else "hidden"
        self.canvas.itemconfigure(self.select_id, state=state)
        if selected:
            self.canvas.tag_raise(self.select_id)
            self.canvas.tag_raise(self.shape_id)
            self.canvas.tag_raise(self.text_id)

    def destroy(self):
        self.canvas.delete(self.shape_id)
        self.canvas.delete(self.text_id)
        self.canvas.delete(self.select_id)

class KeihanControlPanel(ctk.CTk):
    def __init__(self, loop):
        super().__init__()
        self.loop = loop
        self.tracker = KHTracker()
        self.title("Keihan Line Traffic Control Monitor")
        self.geometry("1600x900")
        
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")
        
        self.station_map_coords = {}
        self.train_blocks = {}
        self.station_markers = {} # (LineKey, StationID) -> canvas_id
        
        self.selected_train_wdf = None
        self.selected_station_id = None
        self.is_map_initialized = False
        
        self.setup_ui()
        self.loop.create_task(self.update_data_loop())

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0, minsize=400) # サイドパネルさらに拡張
        self.grid_rowconfigure(0, weight=1)

        # --- Map Area ---
        self.map_frame = ctk.CTkFrame(self, corner_radius=0, fg_color=COLOR_BG)
        self.map_frame.grid(row=0, column=0, sticky="nsew")
        self.map_frame.grid_rowconfigure(0, weight=1)
        self.map_frame.grid_columnconfigure(0, weight=1)
        
        canvas_width = 3200
        canvas_height = 800
        
        self.canvas = ctk.CTkCanvas(
            self.map_frame, bg=COLOR_CANVAS, highlightthickness=0,
            width=canvas_width, height=canvas_height,
            scrollregion=(0, 0, canvas_width, canvas_height)
        )
        
        h_scroll = ctk.CTkScrollbar(self.map_frame, orientation="horizontal", command=self.canvas.xview)
        v_scroll = ctk.CTkScrollbar(self.map_frame, orientation="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)
        
        self.canvas.grid(row=0, column=0, sticky="nsew")
        h_scroll.grid(row=1, column=0, sticky="ew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Button-4>", self.on_mouse_wheel)
        self.canvas.bind("<Button-5>", self.on_mouse_wheel)
        
        self.canvas.tag_bind("train", "<Button-1>", self.on_train_click)
        self.canvas.tag_bind("train_select", "<Button-1>", self.on_train_click)
        self.canvas.tag_bind("station_marker", "<Button-1>", self.on_station_click) # 駅クリック

        # --- Side Panel ---
        self.side_panel = ctk.CTkFrame(self, corner_radius=0, fg_color="#222222")
        self.side_panel.grid(row=0, column=1, sticky="nsew")
        # 0: Header, 1: Stats, 2: TrainList (Expand), 3: Detail, 4: Legend
        self.side_panel.grid_rowconfigure(2, weight=1) 
        
        # Header
        ctk.CTkLabel(self.side_panel, text="TRAFFIC MONITOR", font=("Roboto Medium", 20)).grid(row=0, column=0, sticky="w", padx=10, pady=(15, 5))
        self.lbl_time = ctk.CTkLabel(self.side_panel, text="--:--:--", font=("Consolas", 18), text_color=COLOR_TEXT_SUB)
        self.lbl_time.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))

        # Train List Area
        ctk.CTkLabel(self.side_panel, text="ACTIVE TRAINS", font=("Roboto Medium", 14), text_color="#888888").grid(row=2, column=0, sticky="nw", padx=10, pady=(10, 0))
        
        self.list_frame = ctk.CTkScrollableFrame(self.side_panel, fg_color="#333333", height=200)
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(30, 10))
        # リストアイテム管理用
        self.train_list_buttons = {} # wdf -> button

        # Detail Panel
        ctk.CTkLabel(self.side_panel, text="DETAILS", font=("Roboto Medium", 16), text_color=COLOR_ACCENT).grid(row=3, column=0, sticky="w", padx=10, pady=(10, 5))
        
        self.detail_frame = ctk.CTkFrame(self.side_panel, fg_color="#333333", height=200)
        self.detail_frame.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.detail_frame.pack_propagate(False) # 高さを固定
        
        self.detail_label = ctk.CTkLabel(
            self.detail_frame, 
            text="Select a train or station...",
            justify="left", anchor="nw", font=("Consolas", 13),
            wraplength=350
        )
        self.detail_label.pack(fill="both", expand=True, padx=10, pady=10)

        # Legend Panel
        ctk.CTkLabel(self.side_panel, text="LEGEND", font=("Roboto Medium", 14), text_color="#888888").grid(row=5, column=0, sticky="w", padx=10, pady=(10, 5))
        self.legend_frame = ctk.CTkFrame(self.side_panel, fg_color="transparent")
        self.legend_frame.grid(row=6, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.create_legend()

    def create_legend(self):
        # Grid layout for legend (2 columns)
        for i, (t_type, color) in enumerate(TYPE_COLORS.items()):
            row = i // 2
            col = i % 2
            
            f = ctk.CTkFrame(self.legend_frame, fg_color="transparent")
            f.grid(row=row, column=col, sticky="w", padx=5, pady=2)
            
            box = ctk.CTkCanvas(f, width=12, height=12, bg=color, highlightthickness=0)
            box.pack(side="left", padx=(0, 5))
            lbl = ctk.CTkLabel(f, text=t_type.value, font=("Arial", 11))
            lbl.pack(side="left")

    def draw_map_background(self):
        self.canvas.delete("map_bg")
        self.canvas.delete("station_marker") # マーカーも再描画
        self.station_map_coords = {}
        self.station_markers = {}
        
        if not self.tracker.stations: return

        for line_key, stations in LINES.items():
            config = LAYOUT_CONFIG[line_key]
            base_y = config["y"]
            start_x = config["x_start"]
            step_x = config["x_step"]
            end_x = start_x + (len(stations) - 1) * step_x
            
            # Line Label (Left side)
            self.canvas.create_text(
                start_x - 20, base_y, text=config["label"],
                fill=COLOR_TEXT_SUB, anchor="e", font=("Arial", 16, "bold"), tags="map_bg"
            )
            # Track Line
            self.canvas.create_line(
                start_x - 10, base_y, end_x + 30, base_y,
                fill=COLOR_LINE, width=6, capstyle="round", tags="map_bg"
            )

            for i, st_id in enumerate(stations):
                sx = start_x + i * step_x
                sy = base_y
                self.station_map_coords[(line_key, st_id)] = (sx, sy)
                
                # Station Marker (Clickable)
                tag = f"station_{st_id}"
                mid = self.canvas.create_oval(
                    sx - 6, sy - 6, sx + 6, sy + 6,
                    fill=COLOR_BG, outline=COLOR_STATION, width=2,
                    tags=("map_bg", "station_marker", tag)
                )
                self.station_markers[(line_key, st_id)] = mid
                
                # Name
                st_name = "Unknown"
                if st_id in self.tracker.stations:
                    st_name = self.tracker.stations[st_id].station_name.ja
                
                text_y = sy - 25 if i % 2 == 0 else sy - 40
                self.canvas.create_text(
                    sx, text_y, text=st_name,
                    fill=COLOR_STATION, font=("MSGothic", 9), tags="map_bg"
                )
        
        self.is_map_initialized = True

    async def update_data_loop(self):
        while True:
            try:
                await self.tracker.fetch_pos()
                if not self.is_map_initialized and self.tracker.stations:
                    self.draw_map_background()
                
                if self.is_map_initialized:
                    self.update_gui_content()
                    self.update_train_list()
                    
            except Exception as e:
                print(f"Update Error: {e}")
            await asyncio.sleep(REFRESH_RATE)

    def update_gui_content(self):
        self.lbl_time.configure(text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        # 列車描画更新
        current_wdfs = set(self.tracker.active_trains.keys())
        existing_wdfs = set(self.train_blocks.keys())

        for wdf in existing_wdfs - current_wdfs:
            self.train_blocks[wdf].destroy()
            del self.train_blocks[wdf]

        for wdf, train in self.tracker.active_trains.items():
            cx, cy = self.calculate_coords(train)
            if cx is None:
                if wdf in self.train_blocks:
                    self.train_blocks[wdf].destroy()
                    del self.train_blocks[wdf]
                continue

            if wdf in self.train_blocks:
                self.train_blocks[wdf].update_pos(cx, cy, train)
            else:
                self.train_blocks[wdf] = TrainBlock(self.canvas, train, cx, cy)

            is_selected = (wdf == self.selected_train_wdf)
            self.train_blocks[wdf].set_selected(is_selected)

        # 詳細パネル更新 (列車)
        if self.selected_train_wdf:
            if self.selected_train_wdf in self.tracker.active_trains:
                self.show_train_details(self.tracker.active_trains[self.selected_train_wdf])
            else:
                self.detail_label.configure(text="Train Lost (Out of Service)")
        # 詳細パネル更新 (駅)
        elif self.selected_station_id:
             if self.selected_station_id in self.tracker.stations:
                 self.show_station_details(self.tracker.stations[self.selected_station_id])

    def update_train_list(self):
        """サイドパネルの列車リストを更新"""
        current_wdfs = list(self.tracker.active_trains.keys())
        
        # 削除された列車
        for wdf in list(self.train_list_buttons.keys()):
            if wdf not in current_wdfs:
                self.train_list_buttons[wdf].destroy()
                del self.train_list_buttons[wdf]
        
        # 新規・更新
        for wdf in current_wdfs:
            train = self.tracker.active_trains[wdf]
            text = f"[{train.train_type.value[0]}] {train.train_number} → {train.destination.station_name.ja}"
            color = TYPE_COLORS.get(train.train_type, "#ffffff")
            
            # 選択中の強調
            fg_color = "#444444" if wdf == self.selected_train_wdf else "transparent"
            
            if wdf not in self.train_list_buttons:
                btn = ctk.CTkButton(
                    self.list_frame, text=text, anchor="w",
                    fg_color=fg_color, hover_color="#555555",
                    text_color="white", height=24,
                    command=lambda w=wdf: self.select_train(w)
                )
                btn.pack(fill="x", pady=1)
                self.train_list_buttons[wdf] = btn
            else:
                self.train_list_buttons[wdf].configure(text=text, fg_color=fg_color)

    def select_train(self, wdf):
        self.selected_train_wdf = wdf
        self.selected_station_id = None # 駅選択解除
        self.update_gui_content()
        self.update_train_list()
        
        # マップを中心に持っていくなどの処理も可能
        if wdf in self.train_blocks:
            block = self.train_blocks[wdf]
            self.canvas.xview_moveto(0) # 簡易リセット
            self.canvas.xview_scroll(int(block.current_x - 400), "units") # 動作は未調整

    def select_station(self, station_id):
        self.selected_station_id = station_id
        self.selected_train_wdf = None # 列車選択解除
        self.update_gui_content()
        self.update_train_list() # 選択解除を反映
        
        # マーカーの色を変えるなどの処理
        self.canvas.itemconfigure("station_marker", outline=COLOR_STATION) # 全リセット
        # 該当駅IDを持つマーカーをハイライト
        for key, marker_id in self.station_markers.items():
            if key[1] == station_id:
                self.canvas.itemconfigure(marker_id, outline=COLOR_STATION_SELECTED)

    def calculate_coords(self, train: ActiveTrainData):
        try:
            line_literal, st1, st2 = calc_position(train.location_col, train.location_row)
            gui_line_key = LINE_MAPPING.get(line_literal)
            if not gui_line_key: return None, None

            pos1 = self.station_map_coords.get((gui_line_key, st1))
            if not pos1: return None, None

            offset_y = -8 if train.direction == "up" else 8
            cy = pos1[1] + offset_y
            cx = pos1[0]

            if st2:
                pos2 = self.station_map_coords.get((gui_line_key, st2))
                if pos2:
                    cx = (pos1[0] + pos2[0]) / 2 
            
            return cx, cy
        except:
            return None, None

    def on_train_click(self, event):
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        item = self.canvas.find_closest(x, y)
        tags = self.canvas.gettags(item)
        
        for tag in tags:
            if tag.startswith("train_"):
                try:
                    wdf = int(tag.split("_")[1])
                    self.select_train(wdf)
                    return
                except: pass

    def on_station_click(self, event):
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        item = self.canvas.find_closest(x, y)
        tags = self.canvas.gettags(item)
        
        for tag in tags:
            if tag.startswith("station_"):
                try:
                    # tag: station_123
                    st_id = int(tag.split("_")[1])
                    self.select_station(st_id)
                    return
                except: pass

    def show_train_details(self, train: ActiveTrainData):
        lines = []
        lines.append(f"【列車情報】")
        lines.append(f"種別: {train.train_type.value} ({train.train_number})")
        lines.append(f"行先: {train.destination.station_name.ja}")
        lines.append(f"車両: {train.train_formation if train.train_formation else '不明'}系 ({train.cars}両)")
        lines.append(f"状態: {'停車中' if train.is_stopping else '走行中'}")
        
        try:
            next_stop = train.next_stop_station
            ns_name = next_stop.station_name.ja if next_stop else "不明"
            lines.append(f"次停: {ns_name}")
            if train.delay_minutes > 0:
                lines.append(f"遅延: 約{train.delay_minutes}分")
        except: pass
        
        if train.has_premiumcar:
            lines.append("★ プレミアムカー連結")
            
        self.detail_label.configure(text="\n".join(lines))

    def show_station_details(self, station: StationData):
        lines = []
        lines.append(f"【駅情報】")
        lines.append(f"駅名: {station.station_name.ja} ({station.station_number})")
        lines.append(f"英語: {station.station_name.en}")
        
        # この駅に止まる列車などを出すことも可能だが、
        # ActiveTrainから検索するのは少し重いかもしれないので一旦静的情報のみ
        
        lines.append("-" * 20)
        lines.append("接近列車:")
        
        # 簡易的に到着予定列車を探す
        found = False
        for train in self.tracker.active_trains.values():
            try:
                if train.next_stop_station == station:
                    lines.append(f"・{train.train_type.value} {train.destination.station_name.ja}行")
                    found = True
            except: pass
            
        if not found:
            lines.append("(なし)")

        self.detail_label.configure(text="\n".join(lines))

    def on_mouse_wheel(self, event):
        if event.state & 0x0001: 
             if event.num == 5 or event.delta < 0: self.canvas.yview_scroll(1, "units")
             elif event.num == 4 or event.delta > 0: self.canvas.yview_scroll(-1, "units")
        else: 
             if event.num == 5 or event.delta < 0: self.canvas.xview_scroll(1, "units")
             elif event.num == 4 or event.delta > 0: self.canvas.xview_scroll(-1, "units")

    def on_canvas_press(self, event):
        self.canvas.scan_mark(event.x, event.y)
    def on_canvas_drag(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

async def main():
    loop = asyncio.get_running_loop()
    app = KeihanControlPanel(loop)
    while True:
        try:
            app.update()
            app.update_idletasks()
        except tk.TclError:
            break
        await asyncio.sleep(GUI_UPDATE_RATE)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
