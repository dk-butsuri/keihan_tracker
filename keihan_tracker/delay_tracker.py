from pydantic import BaseModel, field_validator
from typing import Optional, Union
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup as bs
import asyncio
import httpx
import re
from urllib.parse import urljoin

JST = ZoneInfo("Asia/Tokyo")
BASE_URL = "https://transit.yahoo.co.jp/diainfo/area/"

class DelayLine(BaseModel):
    LineName: str
    status: str
    detail: str
    AnnouncedTime: Optional[datetime]

class Line(BaseModel):
    corporationIndex: str
    code: str
    Name: str

class Comment(BaseModel):
    text: str
    status: str

class Prefecture(BaseModel):
    code: str
    Name: str

class Station(BaseModel):
    code: str
    Name: str
    Yomi: str
    Type: str

class Point(BaseModel):
    Prefecture: Prefecture
    Station: Station

class Section(BaseModel):
    Point: list[Point]

class Corporation(BaseModel):
    code: str
    Name: str

# --- 主要な情報のモデル ---

class Information(BaseModel):
    provider: str
    status: str
    Line: Line
    Title: str
    Comment: list[Comment]
    Datetime: datetime
    
    # 修正1: 型定義は最終的に欲しい「list」だけにする（Unionは使わない方が扱いやすい）
    Section: Optional[list["Section"]] = None
    Prefecture: list[Prefecture]

    # 修正2: mode='before' を追加。これにより型チェックの前に実行される
    @field_validator('Section', mode='before')
    @classmethod
    def normalize_section(cls, v):
        """
        入力が単体の辞書(dict)ならリストに変換し、NoneならNoneを返す。
        リストならそのまま返す。
        """
        if v is None:
            return None
        if isinstance(v, dict):
            return [v]
        return v

    # 修正3: こちらも mode='before' を追加
    @field_validator('Prefecture', mode='before')
    @classmethod
    def normalize_prefecture(cls, v):
        """
        入力が単体の辞書(dict)ならリストに変換する。
        """
        if isinstance(v, dict):
            return [v]
        return v

# --- ルート構造の定義 ---
class ResultSetData(BaseModel):
    # ...
    Information: Optional[list["Information"]] = None
    Corporation: Optional[list["Corporation"]] = None

    @field_validator('Information', 'Corporation', mode='before')
    @classmethod
    def normalize_list(cls, v):
        if v is None: return []
        if isinstance(v, dict): return [v] # 辞書ならリストで包む
        return v
    
class ResponseModel(BaseModel):
    ResultSet: ResultSetData


async def get_yahoo_delay(area:int=6) -> list[DelayLine]:
    async with httpx.AsyncClient() as crowler:
        html = await crowler.get(f"{BASE_URL}{area}")

        soup = bs(html.content, "html.parser")
        
        table = soup.select("div.elmTblLstLine.trouble")[0].find_all("table")
        table = table[0] if len(table) != 0 else None
        if not table:
            return []
        
        async def get_detail(url:str) -> tuple[str,str]:
            res = await crowler.get(url,follow_redirects=True)
            soup = bs(res.text, "html.parser")
            info = soup.select("div#mdServiceStatus")[0]
            title = info.select("dl > dt")[0].text
            text = info.select("dl > dd > p")[0].text

            return title, text

        delays:list[DelayLine] = []
        tasks:list = []
        lines:list[tuple[str,str]] = []
        for tr in table.find_all("tr")[1:]:
            url = urljoin(BASE_URL, str(tr.find_all("td")[0].find_all("a")[0].get("href")))
            line = tr.find_all("td")[0].text    # ○○線
            short_status = tr.find_all("td")[2].text  # 17:00頃、宇都宮...
            tasks.append(get_detail(url))
            lines.append((line, short_status))

        for i, line in zip(await asyncio.gather(*tasks), lines):
            line, status = line
            title, text = i
            
            pattern = r"（(?P<month>\d{1,2})月(?P<day>\d{1,2})日\s*(?P<hour>\d{1,2})時(?P<minute>\d{1,2})分掲載）"
            m = re.search(pattern, text) or {}

            dt = datetime(
                year=datetime.now().year,  # 年は別途補完
                month=int(m["month"]),
                day=int(m["day"]),
                hour=int(m["hour"]),
                minute=int(m["minute"]),
                tzinfo=JST
            )
            delays.append(DelayLine(LineName=line, status=title, detail=text, AnnouncedTime=dt))
        return delays
    
async def get_ekispert_delay(api_key:str, prefs:list[int]=[26,27,28]) -> list[DelayLine]:
    async with httpx.AsyncClient() as web:
        uri = f"http://api.ekispert.jp/v1/json/operationLine/service/rescuenow/information?key={api_key}"    
        uri += f"&prefectureCode={':'.join(map(str,prefs))}"
        request = await web.get(uri)
        request.raise_for_status()
        res = ResponseModel.model_validate(request.json())
        
        results=[]
        for i in res.ResultSet.Information or []:
            results.append(
                DelayLine(LineName=i.Line.Name,
                      status=i.status,
                      detail=i.Comment[0].text,
                      AnnouncedTime=i.Datetime
                      )
            )
        return results