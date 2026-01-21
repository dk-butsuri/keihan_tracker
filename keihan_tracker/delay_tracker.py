from pydantic import BaseModel, field_validator, BeforeValidator, Field
from typing import Optional, Union, Annotated, List, Any
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

def force_list(v: Any) -> List[Any]:
    if v is None: return []
    if isinstance(v, list): return v
    return [v]

AsList = BeforeValidator(force_list)

# --- モデル定義 (クラス名を変更して衝突を回避) ---

class LineData(BaseModel):
    Name: str = ""
    code: str = ""
    corporationIndex: str = ""

class CommentData(BaseModel):
    text: str = ""
    status: str = ""

class PrefectureData(BaseModel):
    Name: str = ""
    code: str = ""

class StationData(BaseModel):
    Name: str = ""
    code: str = ""
    Type: str = ""
    Yomi: str = ""

class PointData(BaseModel):
    # フィールド名はJSONに合わせて "Prefecture" だが、型は "PrefectureData" にする
    Prefecture: PrefectureData = Field(default_factory=PrefectureData)
    Station: StationData = Field(default_factory=StationData)

class SectionData(BaseModel):
    Point: Annotated[List[PointData], AsList] = []

class CorporationData(BaseModel):
    Name: str = ""
    code: str = ""

class InformationData(BaseModel):
    status: str = ""
    provider: str = ""
    Title: str = ""
    Datetime: datetime
    
    # ここでもフィールド名と型名を区別する
    Line: LineData = Field(default_factory=LineData)
    
    Section: Annotated[List[SectionData], AsList] = []
    Comment: Annotated[List[CommentData], AsList] = []
    Prefecture: Annotated[List[PrefectureData], AsList] = []

class ResultSetData(BaseModel):
    apiVersion: str = ""
    engineVersion: str = ""
    Information: Annotated[List[InformationData], AsList] = []
    Corporation: Annotated[List[CorporationData], AsList] = []

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
        
        results:dict[str,DelayLine] = {}
        for i in res.ResultSet.Information or []:
            if i.Line.code in results:
                if i.Datetime < (results[i.Line.code].AnnouncedTime or datetime.min):
                    continue
            results[i.Line.code] = DelayLine(
                LineName=i.Line.Name,
                status=i.status,
                detail=i.Comment[0].text,
                AnnouncedTime=i.Datetime
            )
        return list(results.values())