import asyncio
import http.server
import socketserver
import webbrowser
import json
import sys
import os
import datetime

sys.path.append(os.getcwd())

from keihan_tracker import KHTracker
from keihan_tracker.keihan_train import stations_map
from keihan_tracker.keihan_train.tracker import ActiveTrainData

PORT = 8000
DATA_CACHE = None

async def fetch_tracker_data():
    print("Fetching data from Keihan API...")
    tracker = KHTracker()
    await tracker.fetch_pos()
    
    lines = {
        "main": stations_map.HONNSEN_UP,
        "nakanoshima": stations_map.NAKANOSHIMA_UP,
        "uji": stations_map.UJI_UP,
        "katano": stations_map.KATANO_UP
    }
    
    # 駅情報の構築（Upcoming Trains含む）
    all_stations = {}
    for st_id, st_data in tracker.stations.items():
        # Upcoming Trainsの取得
        upcoming_list = []
        try:
            # st_data.upcoming_trains はプロパティ
            raw_upcoming = st_data.upcoming_trains 
            for train, stop in raw_upcoming:
                t_type = train.train_type
                if hasattr(t_type, 'value'):
                    t_type = t_type.value
                
                time_str = stop.time.strftime("%H:%M") if stop.time else "??:??"
                
                # ActiveTrainDataかどうかの判定
                is_active = isinstance(train, ActiveTrainData)
                delay = getattr(train, 'delay_minutes', 0) if is_active else 0
                
                upcoming_list.append({
                    "type": t_type,
                    "dest": str(train.destination) if train.destination else "Unknown",
                    "time": time_str,
                    "number": getattr(train, 'train_number', '-'),
                    "formation": getattr(train, 'train_formation', '-'),
                    "cars": getattr(train, 'cars', '-'),
                    "delay": delay,
                    "is_active": is_active
                })
        except Exception as e:
            print(f"Error getting upcoming trains for {st_id}: {e}")

        all_stations[st_id] = {
            "id": st_id,
            "name": st_data.station_name.ja,
            "upcoming": upcoming_list
        }

    trains_list = []
    for wdf, train in tracker.trains.items():
        stops_data = {}
        
        if hasattr(train, 'route_stations'):
            for s in train.route_stations:
                st_id = s.station.station_number
                time_str = s.time.strftime("%H:%M") if s.time else ""
                
                stops_data[st_id] = {
                    "time": time_str,
                    "is_stop": s.is_stop,
                    "is_start": s.is_start,
                    "is_final": s.is_final
                }
        
        t_type = train.train_type
        if hasattr(t_type, 'value'):
            t_type = t_type.value

        # 現在位置情報の取得
        current_pos = {
            "is_stopping": False,
            "station_id": None,
            "status": "unknown"
        }
        
        try:
            if hasattr(train, 'is_stopping') and hasattr(train, 'next_station'):
                is_stopping = train.is_stopping
                next_st = train.next_station
                
                if next_st:
                    current_pos["is_stopping"] = is_stopping
                    current_pos["station_id"] = next_st.station_number
                    current_pos["status"] = "stopping" if is_stopping else "moving"
        except Exception:
            pass

        train_data = {
            "id": wdf,
            "number": getattr(train, 'train_number', 'Unknown'),
            "type": t_type,
            "dest": str(train.destination) if train.destination else "Unknown",
            "stops": stops_data,
            "direction": getattr(train, 'direction', 'unknown'),
            "line": getattr(train, 'line', 'unknown'),
            "formation": getattr(train, 'train_formation', '-'),
            "cars": getattr(train, 'cars', '-'),
            "current_pos": current_pos
        }
        trains_list.append(train_data)
        
    return {
        "stations": all_stations,
        "lines": lines,
        "trains": trains_list
    }

class DataHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        global DATA_CACHE
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode('utf-8'))
        elif self.path == '/api/data':
            if DATA_CACHE is None:
                self.send_error(503, "Data not ready")
                return
            self.send_response(200)
            self.send_header('Content-type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(DATA_CACHE, ensure_ascii=False).encode('utf-8'))
        else:
            super().do_GET()

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>京阪電車 停車駅確認ツール</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/vue@3/dist/vue.global.js"></script>
    <style>
        /* カスタムスクロールバー */
        ::-webkit-scrollbar { height: 10px; width: 10px; }
        ::-webkit-scrollbar-thumb { background: #94a3b8; border-radius: 5px; border: 2px solid #f1f5f9; }
        ::-webkit-scrollbar-track { background: #f1f5f9; }
        
        /* 縦書き用クラス */
        .writing-mode-vertical {
            writing-mode: vertical-rl;
            text-orientation: mixed;
        }
        
        /* テーブルの固定ヘッダーと列 */
        .sticky-col-header {
            position: sticky;
            top: 0;
            z-index: 30;
            background-color: #f8fafc; /* bg-slate-50 */
        }
        .sticky-row-header {
            position: sticky;
            left: 0;
            z-index: 20;
            background-color: white;
            box-shadow: 2px 0 5px -2px rgba(0,0,0,0.1);
        }
        /* 交差する左上のセル */
        .sticky-corner {
            position: sticky;
            top: 0;
            left: 0;
            z-index: 40;
            background-color: #f8fafc;
            box-shadow: 2px 2px 5px -2px rgba(0,0,0,0.1);
        }

        .station-line-through {
            position: absolute;
            top: 50%;
            left: 0;
            right: 0;
            height: 2px;
            background-color: #e2e8f0;
            z-index: 1;
        }
        
        .station-header:hover {
            background-color: #e0f2fe; /* blue-100 */
            cursor: pointer;
        }
        .station-header:hover .station-name {
            color: #0369a1; /* sky-700 */
        }
    </style>
</head>
<body class="bg-slate-100 text-slate-800 font-sans h-screen flex flex-col overflow-hidden">
    <div id="app" class="flex flex-col h-full">
        <!-- ヘッダーエリア -->
        <header class="flex-none bg-white border-b border-slate-200 px-6 py-3 flex justify-between items-center shadow-sm z-50">
            <div class="flex items-center gap-4">
                <div class="bg-green-700 text-white p-2 rounded-lg shadow">
                    <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                </div>
                <div>
                    <h1 class="text-xl font-bold text-slate-800 tracking-tight">京阪電車 運行モニター</h1>
                    <p class="text-xs text-slate-500 font-medium">リアルタイム停車駅・遅延状況確認</p>
                </div>
            </div>
            <button @click="fetchData" 
                    class="group flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-bold rounded-full hover:bg-blue-700 transition shadow-md hover:shadow-lg active:transform active:scale-95">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 group-hover:animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                データ更新
            </button>
        </header>

        <!-- コントロールパネル -->
        <div class="flex-none bg-white border-b border-slate-200 p-4 shadow-sm z-40">
            <div class="flex flex-wrap gap-6 items-end">
                <div class="space-y-1">
                    <label class="block text-xs font-bold text-slate-500 uppercase tracking-wider">路線選択</label>
                    <select v-model="selectedLine" class="form-select block w-48 rounded-md border-slate-300 bg-slate-50 text-sm focus:border-blue-500 focus:ring-blue-500 py-1.5 px-3 shadow-sm border">
                        <option value="main">京阪本線・鴨東線</option>
                        <option value="nakanoshima">中之島線</option>
                        <option value="uji">宇治線</option>
                        <option value="katano">交野線</option>
                    </select>
                </div>
                
                <div class="space-y-1">
                    <label class="block text-xs font-bold text-slate-500 uppercase tracking-wider">方向</label>
                    <div class="flex bg-slate-100 rounded-md p-1 border border-slate-200">
                        <button @click="selectedDirection = 'all'" :class="['px-3 py-1 text-xs rounded font-medium transition', selectedDirection === 'all' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700']">すべて</button>
                        <button @click="selectedDirection = 'up'" :class="['px-3 py-1 text-xs rounded font-medium transition', selectedDirection === 'up' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700']">上り (京都)</button>
                        <button @click="selectedDirection = 'down'" :class="['px-3 py-1 text-xs rounded font-medium transition', selectedDirection === 'down' ? 'bg-white text-slate-800 shadow-sm' : 'text-slate-500 hover:text-slate-700']">下り (大阪)</button>
                    </div>
                </div>

                <div class="flex-grow space-y-1 overflow-hidden">
                    <label class="block text-xs font-bold text-slate-500 uppercase tracking-wider">種別絞り込み</label>
                    <div class="flex gap-2 overflow-x-auto pb-1 scrollbar-hide">
                        <button 
                            v-for="type in uniqueTypes" 
                            :key="type"
                            @click="toggleType(type)"
                            :class="[
                                'px-3 py-1 rounded-full text-xs font-bold border transition whitespace-nowrap shadow-sm', 
                                selectedTypes.includes(type) 
                                    ? getTrainColorClass(type, true)
                                    : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'
                            ]"
                        >
                            {{ type }}
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <!-- メインコンテンツ (テーブル) -->
        <div class="flex-grow overflow-auto relative bg-slate-50">
            <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-white bg-opacity-80 z-50">
                <div class="flex flex-col items-center">
                    <div class="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mb-4"></div>
                    <div class="text-blue-600 font-bold">データを読み込み中...</div>
                </div>
            </div>

            <table v-else class="w-full border-separate border-spacing-0">
                <thead>
                    <tr>
                        <!-- 左上固定セル -->
                        <th class="sticky-corner p-4 border-b border-r border-slate-200 text-left min-w-[200px] align-bottom">
                            <div class="flex justify-between items-end">
                                <div>
                                    <div class="text-xs text-slate-500 font-medium mb-1">表示中の列車</div>
                                    <div class="text-2xl font-bold text-slate-800 leading-none">{{ filteredTrains.length }}<span class="text-sm font-normal text-slate-500 ml-1">本</span></div>
                                </div>
                            </div>
                        </th>
                        <!-- 駅名ヘッダー -->
                        <th v-for="stId in currentLineStations" :key="stId" 
                            @click="openStationModal(stId)"
                            class="sticky-col-header station-header border-b border-r border-slate-200 p-2 min-w-[36px] max-w-[40px] align-bottom transition duration-150 relative group">
                            <div class="absolute top-1 right-1 opacity-0 group-hover:opacity-100 text-blue-500">
                                <svg xmlns="http://www.w3.org/2000/svg" class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                            </div>
                            <div class="flex flex-col items-center h-40 justify-end pb-2 gap-2">
                                <div class="writing-mode-vertical text-sm font-bold text-slate-700 station-name tracking-wide select-none">
                                    {{ getStationName(stId) }}
                                </div>
                                <div class="text-[9px] font-mono text-slate-400 border border-slate-200 rounded px-0.5 bg-white">
                                    {{ stId }}
                                </div>
                            </div>
                        </th>
                    </tr>
                </thead>
                <tbody>
                    <tr v-for="train in filteredTrains" :key="train.id" class="group hover:bg-blue-50 transition-colors duration-75">
                        <!-- 列車情報列 (固定) -->
                        <td class="sticky-row-header border-b border-r border-slate-200 p-3">
                            <div class="flex flex-col gap-1">
                                <div class="flex items-center gap-2">
                                    <span :class="['px-2 py-0.5 text-xs font-bold rounded text-white shadow-sm', getTrainColorClass(train.type)]">
                                        {{ train.type }}
                                    </span>
                                    <span class="text-xs font-mono text-slate-400">{{ train.number }}</span>
                                </div>
                                <div class="flex justify-between items-baseline mt-1">
                                    <div class="font-bold text-slate-800 truncate max-w-[120px]" :title="train.dest">
                                        {{ train.dest }} <span class="text-xs font-normal text-slate-500">行</span>
                                    </div>
                                    <div class="text-[10px] text-slate-400 font-mono flex flex-col items-end leading-none">
                                        <span>{{ train.formation }}</span>
                                        <span v-if="train.cars !== '-'" class="scale-90 text-slate-500">({{ train.cars }}両)</span>
                                    </div>
                                </div>
                            </div>
                        </td>

                        <!-- 停車駅グリッド -->
                        <td v-for="(stId, idx) in currentLineStations" :key="stId" class="border-b border-r border-slate-100 relative p-0 h-14 text-center group-hover:border-slate-200">
                            <!-- 運行範囲の線 -->
                            <div v-if="isStationInRoute(train, idx)" class="station-line-through group-hover:bg-blue-200 transition-colors"></div>
                            
                            <!-- 走行中インジケーター (矢印) -->
                            <!-- 中央と被らないように左側に配置 -->
                            <div v-if="train.current_pos.station_id === stId && !train.current_pos.is_stopping" 
                                 class="absolute left-0 top-1/2 transform -translate-y-1/2 z-20 ml-0.5 pointer-events-none">
                                 <div class="bg-blue-600 text-white rounded-full p-0.5 shadow-sm animate-bounce">
                                    <svg xmlns="http://www.w3.org/2000/svg" class="h-2.5 w-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M19 14l-7 7m0 0l-7-7m7 7V3" />
                                    </svg>
                                 </div>
                            </div>

                            <!-- 停車情報 -->
                            <div v-if="train.stops[stId]" class="relative z-10 w-full h-full flex items-center justify-center">
                                <!-- 停車する場合 -->
                                <template v-if="train.stops[stId].is_stop">
                                    <div :class="['border-2 rounded-lg px-1.5 py-0.5 shadow-sm transition-all min-w-[32px] duration-300', 
                                        train.current_pos.station_id === stId && train.current_pos.is_stopping 
                                            ? 'bg-red-600 border-red-600 text-white scale-110 shadow-md animate-pulse' 
                                            : 'bg-white border-slate-200 group-hover:border-blue-300']">
                                        <div :class="['text-[11px] font-bold font-mono leading-tight', 
                                            train.current_pos.station_id === stId && train.current_pos.is_stopping ? 'text-white' : 'text-slate-800']">
                                            {{ train.stops[stId].time || '始' }}
                                        </div>
                                    </div>
                                </template>
                                <!-- 通過する場合 -->
                                <template v-else>
                                    <div :class="['w-2 h-2 rounded-full border-2 border-white transition-all',
                                         train.current_pos.station_id === stId 
                                            ? (train.current_pos.is_stopping ? 'bg-red-500 scale-150 ring-2 ring-red-200 animate-pulse' : 'bg-blue-500 scale-150 ring-2 ring-blue-200')
                                            : 'bg-slate-300 group-hover:bg-blue-300']"></div>
                                </template>
                            </div>
                        </td>
                    </tr>
                </tbody>
            </table>

            <div v-if="!loading && filteredTrains.length === 0" class="flex flex-col items-center justify-center h-64 text-slate-400">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-12 w-12 mb-2 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <p>条件に一致する列車はありません</p>
            </div>
        </div>

        <!-- 駅詳細モーダル -->
        <div v-if="showModal" class="fixed inset-0 z-[100] flex items-center justify-center p-4">
            <div class="absolute inset-0 bg-black bg-opacity-50 transition-opacity" @click="closeModal"></div>
            
            <div class="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col relative z-10 overflow-hidden animate-fade-in-up">
                <div class="flex justify-between items-center p-4 border-b bg-slate-50">
                    <div>
                        <div class="text-xs text-slate-500 font-bold mb-1">STATION INFO</div>
                        <h2 class="text-2xl font-bold text-slate-800">{{ modalStationName }} <span class="text-sm font-mono text-slate-400 ml-2">KH{{ modalStationId }}</span></h2>
                    </div>
                    <button @click="closeModal" class="text-slate-400 hover:text-slate-600 p-2 rounded-full hover:bg-slate-200 transition">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>
                
                <div class="p-4 overflow-y-auto bg-slate-50">
                    <h3 class="text-sm font-bold text-slate-600 mb-3 flex items-center">
                        <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 mr-1" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                        発着予定リスト (Upcoming Trains)
                    </h3>
                    
                    <div v-if="modalStationData && modalStationData.length > 0" class="space-y-2">
                        <div v-for="(train, idx) in modalStationData" :key="idx" 
                             class="bg-white p-3 rounded border border-slate-200 shadow-sm flex items-center justify-between hover:border-blue-300 transition">
                            <div class="flex items-center gap-3">
                                <div class="text-xl font-bold text-slate-800 w-16 text-center font-mono">{{ train.time }}</div>
                                <div>
                                    <div class="flex items-center gap-2 mb-1">
                                        <span :class="['px-2 py-0.5 text-xs font-bold rounded text-white shadow-sm', getTrainColorClass(train.type)]">
                                            {{ train.type }}
                                        </span>
                                        <span v-if="train.delay > 0" class="px-1.5 py-0.5 text-[10px] font-bold rounded bg-red-100 text-red-600 border border-red-200 animate-pulse">
                                            遅れ {{ train.delay }}分
                                        </span>
                                    </div>
                                    <div class="text-sm font-bold text-slate-700">
                                        {{ train.dest }} <span class="text-xs font-normal text-slate-500">行</span>
                                    </div>
                                </div>
                            </div>
                            <div class="text-right">
                                <div class="text-xs text-slate-400 font-mono">{{ train.number }}</div>
                                <div class="text-xs text-slate-500 font-mono">{{ train.formation }}</div>
                                <div v-if="train.cars !== '-'" class="text-xs text-slate-500">{{ train.cars }}両</div>
                            </div>
                        </div>
                    </div>
                    <div v-else class="text-center py-8 text-slate-400 bg-white rounded border border-dashed border-slate-300">
                        発着予定情報はありません
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const { createApp, ref, computed, onMounted } = Vue;

        createApp({
            setup() {
                const rawData = ref(null);
                const loading = ref(true);
                const selectedLine = ref('main');
                const selectedDirection = ref('all');
                const selectedTypes = ref([]);
                
                // モーダル用state
                const showModal = ref(false);
                const selectedStationId = ref(null);

                const fetchData = async () => {
                    loading.value = true;
                    try {
                        const res = await fetch('/api/data');
                        if (!res.ok) throw new Error('API Error');
                        rawData.value = await res.json();
                        
                        if (selectedTypes.value.length === 0) {
                             const types = new Set(rawData.value.trains.map(t => t.type));
                             selectedTypes.value = Array.from(types);
                        }
                    } catch (e) {
                        console.error(e);
                        alert('データの取得に失敗しました');
                    } finally {
                        loading.value = false;
                    }
                };

                const currentLineStations = computed(() => {
                    if (!rawData.value) return [];
                    return rawData.value.lines[selectedLine.value] || [];
                });

                const uniqueTypes = computed(() => {
                    if (!rawData.value) return [];
                    return Array.from(new Set(rawData.value.trains.map(t => t.type))).sort();
                });

                const filteredTrains = computed(() => {
                    if (!rawData.value) return [];
                    let trains = rawData.value.trains;

                    const lineMap = {
                        'main': '京阪本線・鴨東線',
                        'nakanoshima': '中之島線',
                        'uji': '宇治線',
                        'katano': '交野線'
                    };
                    trains = trains.filter(t => t.line === lineMap[selectedLine.value]);

                    if (selectedDirection.value !== 'all') {
                        trains = trains.filter(t => t.direction === selectedDirection.value);
                    }

                    if (selectedTypes.value.length > 0) {
                        trains = trains.filter(t => selectedTypes.value.includes(t.type));
                    }
                    
                    trains.sort((a, b) => {
                        // 種別順序の定義
                        const typeOrder = [
                            "快速特急 洛楽", "ライナー", "特急", 
                            "快速急行", "通勤快急", 
                            "急行", "深夜急行", "通勤急行", 
                            "通勤準急", "準急", 
                            "区間急行", 
                            "普通", "臨時列車"
                        ];
                        const typeDiff = typeOrder.indexOf(a.type) - typeOrder.indexOf(b.type);
                        if (typeDiff !== 0) return typeDiff;
                        
                        // それでも同じなら方向、列車番号
                        if (a.direction !== b.direction) return a.direction.localeCompare(b.direction);
                        return a.number.localeCompare(b.number);
                    });

                    return trains;
                });

                const getTrainRouteRange = (train) => {
                    const stations = currentLineStations.value;
                    if (!stations.length) return [-1, -1];
                    let minIdx = Infinity;
                    let maxIdx = -Infinity;
                    
                    Object.keys(train.stops).forEach(stIdStr => {
                        const stId = parseInt(stIdStr);
                        const idx = stations.indexOf(stId);
                        if (idx !== -1) {
                            if (idx < minIdx) minIdx = idx;
                            if (idx > maxIdx) maxIdx = idx;
                        }
                    });
                    
                    if (minIdx === Infinity) return [-1, -1];
                    return [minIdx, maxIdx];
                };

                const isStationInRoute = (train, idx) => {
                    const [min, max] = getTrainRouteRange(train);
                    return idx >= min && idx <= max;
                };

                const getStationName = (id) => {
                    if (!rawData.value) return id;
                    const st = rawData.value.stations[id];
                    return st ? st.name : id;
                };
                
                const toggleType = (type) => {
                    if (selectedTypes.value.includes(type)) {
                        selectedTypes.value = selectedTypes.value.filter(t => t !== type);
                    } else {
                        selectedTypes.value.push(type);
                    }
                };

                const getTrainColorClass = (type, isButton = false) => {
                    const colors = {
                        "快速特急 洛楽": "bg-red-600 border-red-600",
                        "ライナー": "bg-red-600 border-red-600",
                        "特急": "bg-red-600 border-red-600",
                        "快速急行": "bg-purple-600 border-purple-600",
                        "通勤快急": "bg-purple-600 border-purple-600",
                        "急行": "bg-orange-500 border-orange-500",
                        "深夜急行": "bg-orange-500 border-orange-500",
                        "通勤急行": "bg-orange-500 border-orange-500",
                        "通勤準急": "bg-blue-600 border-blue-600",
                        "準急": "bg-blue-600 border-blue-600",
                        "区間急行": "bg-green-600 border-green-600",
                        "普通": "bg-slate-600 border-slate-600",
                        "臨時列車": "bg-pink-500 border-pink-500"
                    };
                    
                    const baseClass = colors[type] || "bg-slate-500 border-slate-500";
                    
                    if (isButton) {
                        return `${baseClass} text-white`;
                    }
                    return baseClass;
                };

                // モーダル関連
                const openStationModal = (stId) => {
                    selectedStationId.value = stId;
                    showModal.value = true;
                };
                
                const closeModal = () => {
                    showModal.value = false;
                    selectedStationId.value = null;
                };

                const modalStationName = computed(() => getStationName(selectedStationId.value));
                const modalStationId = computed(() => selectedStationId.value);
                
                const modalStationData = computed(() => {
                    if (!rawData.value || !selectedStationId.value) return [];
                    const st = rawData.value.stations[selectedStationId.value];
                    return st ? st.upcoming : [];
                });

                onMounted(() => {
                    fetchData();
                });

                return {
                    rawData,
                    loading,
                    selectedLine,
                    selectedDirection,
                    selectedTypes,
                    uniqueTypes,
                    currentLineStations,
                    filteredTrains,
                    showModal,
                    modalStationName,
                    modalStationId,
                    modalStationData,
                    fetchData,
                    getStationName,
                    toggleType,
                    isStationInRoute,
                    getTrainColorClass,
                    openStationModal,
                    closeModal
                };
            }
        }).mount('#app');
    </script>
</body>
</html>
"""

def main():
    global DATA_CACHE
    print("Initializing...")
    try:
        DATA_CACHE = asyncio.run(fetch_tracker_data())
        print(f"Loaded {len(DATA_CACHE['trains'])} trains.")
    except Exception as e:
        print(f"Error fetching data: {e}")
        return

    with socketserver.TCPServer(('', PORT), DataHandler) as httpd:
        url = f"http://localhost:{PORT}"
        print(f"Server started at {url}")
        print("Press Ctrl+C to stop.")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
            httpd.shutdown()

if __name__ == "__main__":
    main()