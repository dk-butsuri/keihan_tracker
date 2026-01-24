# GEMINI VIBE CODING

from typing import Optional
from .schemes import LineLiteral

def calc_position(col: int, row: int) -> tuple[LineLiteral, int, Optional[int]]:
    """座標から、停車中の駅番号、もしくは2駅の駅番号を返します。"""
    line: LineLiteral
    
    # --- 1. 路線判定 ---
    if 1 <= row <= 131:
        # Row 118以降かつcol 1,2は中之島線
        if row >= 118 and (col == 1 or col == 2):
            line = "中之島線"
        else:
            line = "京阪本線・鴨東線"
    elif 132 <= row <= 153:
        line = "宇治線"
    elif 154 <= row <= 175:
        line = "交野線"
    else:
        raise ValueError(f"Inputed row {row} is out of range.")
    
    # colのバリデーション
    if col <= 0 or col >= 6:
        raise ValueError(f"Inputed col {col} is out of range.")
    

    # --- 2. 駅IDと状態の計算 ---

    # === A. 京阪本線・鴨東線・中之島線 (Row 1-131) ===
    if row <= 131:
        # A-1. 通常区間 (Row 1-117)
        if row <= 117:
            block_index = (row - 1) // 3
            current_station = 42 - block_index
            pos_in_block = (row - 1) % 3  # 0:Top, 1:Mid, 2:Bot
            
            is_stopped = False
            
            # 基本: Top(0)は停車
            if pos_in_block == 0:
                is_stopped = True
            
            # 特例: 2行目(Mid)以降も停車扱いになる駅 (HTMLの big-area/special-area 等に基づく)
            # 出町柳(42): Row 2も停車
            if current_station == 42 and pos_in_block == 1: is_stopped = True
            # 枚方市(21): Row 65(Mid)も停車
            if current_station == 21 and pos_in_block == 1: is_stopped = True
            # 京橋(4): Row 116(Mid)も停車
            if current_station == 4 and pos_in_block == 1: is_stopped = True
            
            if is_stopped:
                return (line, current_station, None)
            else:
                # 移動中 (当駅 -> 次駅)
                # Next station is current - 1
                return (line, current_station, current_station - 1)

        # A-2. 地下・中之島線区間 (Row 118-131)
        # この区間はHTML上隙間なく special-area が続くため、すべて停車扱いとする
        else:
            # 共通: 天満橋(3) Row 118-120
            if row <= 120: return (line, 3, None)

            if line == "中之島線":
                if row <= 123: return (line, 51, None) # なにわ橋
                elif row <= 126: return (line, 52, None) # 大江橋
                elif row <= 129: return (line, 53, None) # 渡辺橋
                else: return (line, 54, None) # 中之島
            
            else: # 京阪本線 大阪側
                if row <= 123: return (line, 2, None) # 北浜
                elif row <= 126: return (line, 1, None) # 淀屋橋
                # Row 127以降は本線には無し
                else: raise ValueError(f"Invalid row {row} for Main Line")

    # === B. 宇治線 (Row 132-153) ===
    elif row <= 153:
        # 中書島(28) Row 132-134 (始発)
        if row <= 134:
            # HTML: 132(special), 133(big) -> 停車
            pos = row - 132
            if pos <= 1: return (line, 28, None)
            else: return (line, 28, 71) # 発車
            
        # 終点: 宇治(77)
        if row == 153: return (line, 77, None)
            
        # 通常駅 (KH71-KH76)
        block_idx = (row - 135) // 3
        current_station = 71 + block_idx
        pos_in_block = (row - 135) % 3
        
        # 基本: Top(0)のみ停車
        if pos_in_block == 0:
            return (line, current_station, None)
        else:
            return (line, current_station, current_station + 1)

    # === C. 交野線 (Row 154-175) ===
    elif row <= 175:
        # 枚方市(21) Row 154-156 (始発)
        if row <= 156:
            # HTML: 154(special), 155(big) -> 停車
            pos = row - 154
            if pos <= 1: return (line, 21, None)
            else: return (line, 21, 61) # 発車
            
        # 終点: 私市(67)
        if row == 175: return (line, 67, None)
            
        # 通常駅 (KH61-KH66)
        block_idx = (row - 157) // 3
        current_station = 61 + block_idx
        pos_in_block = (row - 157) % 3
        
        # 基本: Top(0)のみ停車
        if pos_in_block == 0:
            return (line, current_station, None)
        else:
            return (line, current_station, current_station + 1)

    return (line, 0, None)

if __name__ == "__main__":
    col,row=map(int,input().split())
    r = calc_position(col,row)
    print(r)